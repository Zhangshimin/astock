from __future__ import annotations

import argparse
import ctypes
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import winsound
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from tkinter import simpledialog, ttk
from typing import Literal

import pystray
from PIL import Image, ImageDraw, ImageGrab

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astock_hub.intraday import (
    CHINA_TZ,
    EquityEvent,
    GlobalMarketContext,
    IntradayContext,
    MacdPoint,
    MacdSignal,
    MinuteBar,
    OrderBook,
    StockBoard,
    calculate_macd,
    completed_bars,
    default_stock_board,
    fetch_equity_event,
    fetch_global_market_context,
    fetch_intraday_context,
    fetch_minute_bars,
    fetch_order_book,
    fetch_sina_history_bars,
    fetch_stock_boards,
    fetch_tencent_stock_change,
    latest_macd_signal,
    normalize_stock_code,
)
from astock_hub.intraday_backtest import (
    CANDIDATE_RULES,
    BacktestEvent,
    BacktestResult,
    BacktestTrade,
    IntradayStrategyConfig,
    actual_buyback_reminder_sides,
    candidate_sides,
    ema_values,
    guarded_reverse_t_action_sides,
    histogram_shrink_sides,
    run_backtest,
    session_vwap_values,
)
from astock_hub.intraday_journal import TradeJournal
from astock_hub.desktop_settings import read_settings, display_allowed, strategy_config, write_status
from astock_hub.taskbar_position import calculate_taskbar_position
from astock_hub.chameleon import background_color, foreground_color
from astock_hub.audio_alerts import AudioGate
from astock_hub.desktop_paths import user_data_dir
import json

DashboardDataMessage = tuple[
    Literal["data"], str, list[MinuteBar], list[MacdPoint], MacdSignal | None
]
DashboardErrorMessage = tuple[Literal["error"], str]
DashboardMessage = DashboardDataMessage | DashboardErrorMessage

_TASKBAR_MUTEX: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A 股分钟线 MACD 金叉/死叉实时弹窗")
    parser.add_argument("code", nargs="?", help="6 位股票代码，例如 600519")
    parser.add_argument("--poll", type=int, default=5, help="轮询间隔秒数，默认 5")
    parser.add_argument("--once", action="store_true", help="只检查一次后退出")
    parser.add_argument("--settings", type=Path, help="uTools 运行配置 JSON（自动热更新）")
    parser.add_argument(
        "--no-popup", action="store_true", help="只打印信号，不显示弹窗"
    )
    parser.add_argument(
        "--gui", action="store_true", help="显示可最小化到任务栏的分时/MACD 窗口"
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="兼容参数：显示贴近任务栏的行情条",
    )
    parser.add_argument(
        "--bar", action="store_true", help="显示贴近任务栏的分时/MACD 行情条"
    )
    return parser.parse_args()


def show_popup(title: str, message: str, warning: bool = False) -> None:
    if sys.platform != "win32":
        return
    icon = 0x30 if warning else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, title, icon | 0x00040000)


def _speak_text(text: str) -> None:
    """后台语音播报。播报文本经 stdin 传入而非命令行拼接，杜绝 PowerShell 注入面。"""
    if sys.platform != "win32":
        return
    shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if shell is None:
        return
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "[Console]::InputEncoding = [Text.Encoding]::UTF8; "
        "$text = [Console]::In.ReadToEnd(); "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Speak($text); $s.Dispose()"
    )
    try:
        process = subprocess.Popen(
            [shell, "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return
    try:
        process.communicate(input=text.encode("utf-8"), timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        try:
            process.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def notify(name: str, code: str, signal: MacdSignal, popup: bool) -> None:
    action = "买入观察（金叉）" if signal.side == "BUY" else "卖出观察（死叉）"
    message = (
        f"{name}（{code}）\n"
        f"时间：{signal.time:%Y-%m-%d %H:%M}\n"
        f"信号：{action}\n"
        f"价格：{signal.price:.2f}\n"
        f"DIF：{signal.dif:.4f}  DEA：{signal.dea:.4f}"
    )
    print(
        f"\n[{datetime.now(CHINA_TZ):%H:%M:%S}] {message.replace(chr(10), ' | ')}",
        flush=True,
    )
    if popup:
        thread = threading.Thread(
            target=show_popup,
            args=(f"MACD {action}", message, signal.side == "SELL"),
            daemon=True,
        )
        thread.start()


def check_once(code: str):
    name, bars = fetch_minute_bars(code)
    closed = completed_bars(bars)
    points = calculate_macd(closed)
    if not points:
        raise RuntimeError("当前没有已完成的分钟行情")
    return name, points[-1], latest_macd_signal(points)


class FlashWindowInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


class DesktopSettingsMixin:
    """Apply uTools settings on the Tk thread and acknowledge visible windows."""

    def _alerts_allowed(self, now: datetime) -> bool:
        settings = self.desktop_settings
        return settings["notifications"] and display_allowed({
            **settings, "displayMode": "scheduled",
            "displayStart": settings["alertStart"], "displayEnd": settings["alertEnd"],
        }, now)

    def _init_settings(self, config_path: Path | None, poll_seconds: int) -> None:
        self.audio_gate = AudioGate()
        self.config_path = config_path
        self.desktop_settings = read_settings(config_path)
        if config_path is None:
            self.desktop_settings["poll"] = poll_seconds
        self._settings_text = None

    def _allow_audio(self, event: str, guarded: bool = True) -> bool:
        return self._alerts_allowed(datetime.now(CHINA_TZ)) and self.audio_gate.allow(
            self.desktop_settings, event, guarded, time.monotonic()
        )

    def _start_settings_watch(self) -> None:
        self._reload_settings()
        self.root.after(1000, self._watch_settings)

    def _watch_settings(self) -> None:
        try:
            self._reload_settings()
        except (OSError, ValueError, tk.TclError) as exc:
            print(f"配置更新失败：{exc}", file=sys.stderr, flush=True)
        if self.root.winfo_exists():
            self.root.after(1000, self._watch_settings)

    def _reload_settings(self) -> None:
        text = self.config_path.read_text(encoding="utf-8") if self.config_path else ""
        if text != self._settings_text:
            settings = read_settings(self.config_path) if self.config_path else self.desktop_settings
            old = self.desktop_settings
            self.desktop_settings = settings
            self.poll_seconds = settings["poll"]
            self.root.attributes("-topmost", settings["topmost"])
            if hasattr(self, "candidate_mode"):
                self.candidate_mode = settings["candidateMode"]
                self.candidate_mode_var.set(self.candidate_mode)
                self.strategy_config = strategy_config(settings)
                if settings.get("symbol") and settings["symbol"] != self.code:
                    self.code_var.set(settings["symbol"])
                    self._change_code()
                if old.get("sectorCode") != settings["sectorCode"]:
                    self.selected_board = None
                    self.context_updated = 0
                    self.context_snapshot = None
                if self.order_book_enabled != settings["orderBook"]:
                    self.order_book_var.set(settings["orderBook"])
                    self._toggle_order_book()
                self.refresh_event.set()
                if display_allowed(settings, datetime.now(CHINA_TZ)):
                    self._dock_inside_taskbar()
                else:
                    self.root.withdraw()
            else:
                self.show_notifications = settings["notifications"]
            self._settings_text = text
        self.root.update_idletasks()
        write_status(self.config_path, {
            "requestId": self.desktop_settings.get("requestId"), "ready": True,
            "visible": bool(self.root.winfo_viewable()), "symbol": self.code,
            "displayMode": self.desktop_settings["displayMode"],
        })


class MacdDashboard(DesktopSettingsMixin):
    def __init__(
        self, code: str | None, poll_seconds: int, show_notifications: bool,
        config_path: Path | None = None,
    ) -> None:
        self._init_settings(config_path, poll_seconds)
        if config_path is None:
            self.desktop_settings["notifications"] = show_notifications
        if sys.platform == "win32":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "astock.macd.dashboard"
            )
        self.root = tk.Tk()
        self.root.withdraw()
        raw_code = code or simpledialog.askstring(
            "选择个股", "请输入 6 位股票代码（如 600519）：", parent=self.root
        )
        if not raw_code:
            raise ValueError("未选择股票")
        self.code = normalize_stock_code(raw_code)
        self.poll_seconds = poll_seconds
        self.show_notifications = show_notifications
        self.messages: queue.Queue[DashboardMessage] = queue.Queue()
        self.stop_event = threading.Event()
        self.last_processed = None
        self.bars: list[MinuteBar] = []
        self.points: list[MacdPoint] = []
        self.name = self.code
        self._build_window()
        self._start_settings_watch()

    def _build_window(self) -> None:
        self.root.title(f"AStock MACD - {self.code}")
        self.root.geometry("760x560")
        self.root.minsize(560, 460)
        self.root.configure(bg="#17191d")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=5)
        self.root.grid_rowconfigure(2, weight=2)
        self.root.grid_rowconfigure(3, weight=3)

        icon = tk.PhotoImage(width=32, height=32)
        icon.put("#17191d", to=(0, 0, 32, 32))
        icon.put("#36c98f", to=(5, 17, 10, 27))
        icon.put("#e5b94f", to=(13, 10, 18, 27))
        icon.put("#e45b65", to=(21, 5, 26, 27))
        self.root.iconphoto(True, icon)
        self.icon = icon

        header = tk.Frame(self.root, bg="#22252b", height=62)
        header.grid(row=0, column=0, sticky="nsew")
        header.grid_columnconfigure(1, weight=1)
        self.title_label = tk.Label(
            header,
            text=self.code,
            bg="#22252b",
            fg="#f2f3f5",
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        self.title_label.grid(
            row=0, column=0, rowspan=2, padx=(16, 24), pady=10, sticky="w"
        )
        self.price_label = tk.Label(
            header,
            text="等待行情",
            bg="#22252b",
            fg="#b8bec8",
            font=("Microsoft YaHei UI", 12),
        )
        self.price_label.grid(row=0, column=1, padx=4, pady=(9, 0), sticky="w")
        self.status_label = tk.Label(
            header,
            text="连接中",
            bg="#22252b",
            fg="#8f98a6",
            font=("Microsoft YaHei UI", 9),
        )
        self.status_label.grid(row=1, column=1, padx=4, pady=(0, 9), sticky="w")

        self.price_canvas = tk.Canvas(self.root, bg="#17191d", highlightthickness=0)
        self.price_canvas.grid(row=1, column=0, sticky="nsew", padx=12, pady=(10, 4))
        self.volume_canvas = tk.Canvas(self.root, bg="#17191d", highlightthickness=0)
        self.volume_canvas.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        self.macd_canvas = tk.Canvas(self.root, bg="#17191d", highlightthickness=0)
        self.macd_canvas.grid(row=3, column=0, sticky="nsew", padx=12, pady=(4, 10))
        self.price_canvas.bind("<Configure>", lambda _event: self._draw_charts())
        self.volume_canvas.bind("<Configure>", lambda _event: self._draw_charts())
        self.macd_canvas.bind("<Configure>", lambda _event: self._draw_charts())
        self.root.deiconify()

    def run(self) -> int:
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._consume_messages)
        self.root.mainloop()
        return 0

    def close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                name, bars = fetch_minute_bars(self.code, timeout=self.desktop_settings["quoteTimeout"])
                closed = completed_bars(bars)
                points = calculate_macd(closed)
                if not points:
                    raise RuntimeError("当前没有已完成的分钟行情")
                latest_date = points[-1].time.date()
                today_points = [
                    point for point in points if point.time.date() == latest_date
                ]
                today_bars = [
                    bar for bar in closed if bar.time.date() == latest_date
                ]
                self.messages.put(
                    ("data", name, today_bars, today_points, latest_macd_signal(points))
                )
            except (OSError, RuntimeError, ValueError) as exc:
                self.messages.put(("error", str(exc)))
            self.stop_event.wait(self.poll_seconds)

    def _consume_messages_once(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == "error":
                    self.status_label.config(
                        text=f"行情获取失败：{message[1]}", fg="#e45b65"
                    )
                    continue
                _, name, bars, points, signal = message
                latest = points[-1]
                is_new_bar = latest.time != self.last_processed
                self.name = name
                self.bars = bars
                self.points = points
                state = "MACD 多头" if latest.histogram > 0 else "MACD 空头"
                state_color = "#36c98f" if latest.histogram > 0 else "#e45b65"
                latest_volume = bars[-1].volume
                average_volume = (
                    sum(bar.volume for bar in bars[-5:]) / min(len(bars), 5)
                )
                volume_ratio = latest_volume / average_volume if average_volume else 0
                self.title_label.config(text=f"{name}  {self.code}")
                self.price_label.config(text=f"{latest.close:.2f}", fg="#f2f3f5")
                self.status_label.config(
                    text=(
                        f"{latest.time:%H:%M}  {state}  "
                        f"量 {latest_volume / 10000:.1f}万股 ({volume_ratio:.1f}x)  "
                        f"DIF {latest.dif:.4f}  DEA {latest.dea:.4f}"
                    ),
                    fg=state_color,
                )
                self.root.title(f"{name} {latest.close:.2f} | {state}")
                self._draw_charts()
                if (
                    is_new_bar
                    and signal is not None
                    and signal.time.date() == datetime.now(CHINA_TZ).date()
                    and self._alerts_allowed(datetime.now(CHINA_TZ))
                ):
                    self._signal_alert(signal)
                self.last_processed = latest.time
        except queue.Empty:
            pass

    def _consume_messages(self) -> None:
        """薄包装：渲染异常只跳过本轮，绝不打断 200ms 重调度。"""
        try:
            self._consume_messages_once()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 看板渲染异常，已跳过本轮：{exc}", file=sys.stderr)
        try:
            if self.root.winfo_exists():
                self.root.after(200, self._consume_messages)
        except tk.TclError:
            pass

    def _draw_charts(self) -> None:
        if not self.points:
            return
        self._draw_price()
        self._draw_volume()
        self._draw_macd()

    @staticmethod
    def _coordinates(
        values: list[float],
        width: int,
        height: int,
        padding: int = 28,
        bounds: tuple[float, float] | None = None,
    ) -> list[float]:
        low, high = bounds or (min(values), max(values))
        span = high - low or 1
        usable_width = max(width - padding * 2, 1)
        usable_height = max(height - padding * 2, 1)
        result = []
        for index, value in enumerate(values):
            x = padding + usable_width * index / max(len(values) - 1, 1)
            y = padding + usable_height * (high - value) / span
            result.extend((x, y))
        return result

    def _draw_price(self) -> None:
        canvas = self.price_canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        canvas.create_rectangle(0, 0, width, height, fill="#17191d", outline="")
        values = [point.close for point in self.points]
        ema20 = ema_values(values, 20)
        vwap = session_vwap_values(self.bars)
        visible_values = values + ema20 + vwap
        low, high = min(visible_values), max(visible_values)
        canvas.create_text(
            8,
            8,
            anchor="nw",
            text="分时价格  ·  EMA20 / VWAP",
            fill="#8f98a6",
            font=("Microsoft YaHei UI", 9),
        )
        canvas.create_text(
            width - 8, 8, anchor="ne", text=f"{high:.2f}", fill="#8f98a6"
        )
        canvas.create_text(
            width - 8,
            height - 8,
            anchor="se",
            text=f"{low:.2f}",
            fill="#8f98a6",
        )
        coordinates = self._coordinates(values, width, height, bounds=(low, high))
        ema_coordinates = self._coordinates(ema20, width, height, bounds=(low, high))
        vwap_coordinates = self._coordinates(vwap, width, height, bounds=(low, high))
        if len(vwap_coordinates) >= 4:
            canvas.create_line(*vwap_coordinates, fill="#aeb6c2", width=1)
        if len(ema_coordinates) >= 4:
            canvas.create_line(*ema_coordinates, fill="#e5b94f", width=1)
        if len(coordinates) >= 4:
            canvas.create_line(*coordinates, fill="#55b6d7", width=2, smooth=True)

    def _draw_volume(self) -> None:
        canvas = self.volume_canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        canvas.create_rectangle(0, 0, width, height, fill="#17191d", outline="")
        if not self.bars:
            return
        volumes = [max(bar.volume, 0) for bar in self.bars]
        maximum = max(max(volumes), 1)
        padding = 28
        usable_height = max(height - padding - 8, 1)
        step = max((width - padding * 2) / max(len(volumes), 1), 1)
        canvas.create_text(
            8,
            8,
            anchor="nw",
            text="成交量  ·  每分钟成交量（万股）",
            fill="#8f98a6",
            font=("Microsoft YaHei UI", 9),
        )
        canvas.create_text(
            width - 8,
            8,
            anchor="ne",
            text=f"{maximum / 10000:.2f}万股",
            fill="#8f98a6",
        )
        for index, volume in enumerate(volumes):
            x = padding + step * (index + 0.5)
            bar_height = usable_height * volume / maximum
            previous_close = (
                self.points[index - 1].close if index > 0 and index - 1 < len(self.points)
                else self.points[index].close
            )
            close = self.points[index].close if index < len(self.points) else previous_close
            color = "#e45b65" if close >= previous_close else "#36c98f"
            canvas.create_rectangle(
                max(padding, x - max(step * 0.35, 1)),
                height - 6 - bar_height,
                min(width - padding, x + max(step * 0.35, 1)),
                height - 6,
                fill=color,
                outline="",
            )

    def _draw_macd(self) -> None:
        canvas = self.macd_canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        canvas.create_rectangle(0, 0, width, height, fill="#17191d", outline="")
        histograms = [point.histogram for point in self.points]
        all_values = (
            histograms
            + [point.dif for point in self.points]
            + [point.dea for point in self.points]
        )
        low, high = min(min(all_values), 0), max(max(all_values), 0)
        span = high - low or 1
        padding = 28
        zero_y = padding + max(height - padding * 2, 1) * high / span
        canvas.create_text(
            8,
            8,
            anchor="nw",
            text="MACD  ·  柱=动能  DIF/DEA=趋势交叉",
            fill="#8f98a6",
            font=("Microsoft YaHei UI", 9),
        )
        canvas.create_line(padding, zero_y, width - padding, zero_y, fill="#4a4f58")
        step = max((width - padding * 2) / max(len(histograms), 1), 1)
        for index, value in enumerate(histograms):
            x = padding + step * (index + 0.5)
            y = padding + max(height - padding * 2, 1) * (high - value) / span
            canvas.create_line(
                x,
                zero_y,
                x,
                y,
                fill="#36c98f" if value >= 0 else "#e45b65",
                width=min(5, max(1, int(step * 0.65))),
            )
        bounds = (low, high)
        dif_coords = self._coordinates(
            [point.dif for point in self.points], width, height, padding, bounds
        )
        dea_coords = self._coordinates(
            [point.dea for point in self.points], width, height, padding, bounds
        )
        if len(dif_coords) >= 4:
            canvas.create_line(*dif_coords, fill="#e5b94f", width=1.5)
            canvas.create_line(*dea_coords, fill="#55b6d7", width=1.5)

    def _signal_alert(self, signal: MacdSignal) -> None:
        action = "买入观察（金叉）" if signal.side == "BUY" else "卖出观察（死叉）"
        color = "#36c98f" if signal.side == "BUY" else "#e45b65"
        print(
            f"[{datetime.now(CHINA_TZ):%H:%M:%S}] {self.name} {signal.time:%H:%M} {action} {signal.price:.2f}",
            flush=True,
        )
        if not self.show_notifications:
            return
        if self._allow_audio(signal.side, guarded=False):
            if self.desktop_settings["sound"]:
                threading.Thread(target=MacdTaskbarBar._play_alert_sound, args=(signal.side,), daemon=True).start()
            if self.desktop_settings["speech"]:
                threading.Thread(target=_speak_text, args=(f"{action}，价格 {signal.price:.2f}",), daemon=True).start()
        self._flash_taskbar()
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#22252b")
        tk.Frame(toast, bg=color, width=5).pack(side="left", fill="y")
        body = tk.Frame(toast, bg="#22252b", padx=16, pady=12)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text=action,
            bg="#22252b",
            fg=color,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body,
            text=f"{self.name} {self.code}   {signal.price:.2f}\n{signal.time:%H:%M}  DIF {signal.dif:.4f}  DEA {signal.dea:.4f}",
            bg="#22252b",
            fg="#f2f3f5",
            justify="left",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(5, 0))
        toast.update_idletasks()
        screen_width = toast.winfo_screenwidth()
        screen_height = toast.winfo_screenheight()
        width, height = 330, 112
        toast.geometry(
            f"{width}x{height}+{screen_width - width - 18}+{screen_height - height - 58}"
        )
        toast.after(6000, toast.destroy)

    def _flash_taskbar(self) -> None:
        if not self.desktop_settings["flash"]:
            return
        if sys.platform != "win32":
            return
        info = FlashWindowInfo(
            ctypes.sizeof(FlashWindowInfo), self.root.winfo_id(), 0x0000000F, 5, 0
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def run_gui(code: str | None, poll_seconds: int, show_notifications: bool, config_path: Path | None = None) -> int:
    try:
        return MacdDashboard(code, poll_seconds, show_notifications, config_path).run()
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2


class MacdTray:
    def __init__(self, code: str, poll_seconds: int, show_notifications: bool) -> None:
        self.code = normalize_stock_code(code)
        self.poll_seconds = poll_seconds
        self.show_notifications = show_notifications
        self.name = self.code
        self.status = "等待行情"
        self.last_signal = "最近信号：暂无"
        self.last_processed = None
        self.last_error = None
        self.points: list[MacdPoint] = []
        self.market_state = "neutral"
        self.icon_mode = "price"
        self.signal_side = None
        self.signal_until = 0.0
        self.stop_event = threading.Event()
        self.check_lock = threading.Lock()
        self.state_lock = threading.Lock()
        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: self.status, None, enabled=False),
            pystray.MenuItem(
                lambda _item: (
                    f"当前图标：{'分时线' if self.icon_mode == 'price' else 'MACD'}"
                ),
                None,
                enabled=False,
            ),
            pystray.MenuItem(lambda _item: self.last_signal, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即检查", self._check_now),
            pystray.MenuItem("更换股票", self._change_stock),
            pystray.MenuItem("退出", self._quit),
        )
        self.icon = pystray.Icon(
            "astock_macd",
            self._create_icon("neutral"),
            f"AStock MACD - {self.code}",
            menu,
        )

    @staticmethod
    def _create_icon(state: str) -> Image.Image:
        accent = {"bull": "#36c98f", "bear": "#e45b65"}.get(state, "#8f98a6")
        image = Image.new("RGBA", (64, 64), (23, 25, 29, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (3, 3, 60, 60), radius=10, fill="#22252b", outline="#4a4f58", width=2
        )
        draw.line((10, 45, 54, 45), fill="#f2f3f5", width=2)
        draw.rectangle((13, 34, 21, 45), fill=accent)
        draw.rectangle((28, 23, 36, 45), fill=accent)
        draw.rectangle((43, 13, 51, 45), fill=accent)
        return image

    @staticmethod
    def _create_market_icon(
        points: list[MacdPoint], mode: str, state: str
    ) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (23, 25, 29, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (2, 2, 61, 61), radius=9, fill="#17191d", outline="#4a4f58", width=2
        )
        if not points:
            draw.line((12, 32, 52, 32), fill="#8f98a6", width=3)
            return image
        if mode == "price":
            recent = points[-36:]
            values = [point.close for point in recent]
            low, high = min(values), max(values)
            span = high - low or 1
            coordinates = []
            for index, value in enumerate(values):
                x = 7 + 50 * index / max(len(values) - 1, 1)
                y = 52 - 40 * (value - low) / span
                coordinates.append((x, y))
            if len(coordinates) > 1:
                draw.line(coordinates, fill="#55b6d7", width=3, joint="curve")
            dot_color = "#36c98f" if state == "bull" else "#e45b65"
            x, y = coordinates[-1]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=dot_color)
        else:
            recent = points[-20:]
            values = [point.histogram for point in recent]
            maximum = max(max(abs(value) for value in values), 0.000001)
            zero_y = 32
            step = 50 / max(len(values), 1)
            draw.line((7, zero_y, 57, zero_y), fill="#8f98a6", width=1)
            for index, value in enumerate(values):
                x = 7 + step * (index + 0.5)
                y = zero_y - value / maximum * 22
                color = "#36c98f" if value >= 0 else "#e45b65"
                draw.line((x, zero_y, x, y), fill=color, width=max(2, int(step * 0.65)))
        return image

    @staticmethod
    def _create_signal_icon(side: str) -> Image.Image:
        buy = side == "BUY"
        accent = "#36c98f" if buy else "#e45b65"
        image = Image.new("RGBA", (64, 64), (23, 25, 29, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (2, 2, 61, 61), radius=10, fill=accent, outline="#f2f3f5", width=3
        )
        if buy:
            arrow = [
                (32, 9),
                (53, 33),
                (41, 33),
                (41, 54),
                (23, 54),
                (23, 33),
                (11, 33),
            ]
        else:
            arrow = [
                (23, 10),
                (41, 10),
                (41, 31),
                (53, 31),
                (32, 55),
                (11, 31),
                (23, 31),
            ]
        draw.polygon(arrow, fill="#ffffff")
        return image

    def run(self) -> int:
        self.icon.run(setup=self._setup)
        return 0

    def _setup(self, icon) -> None:
        icon.visible = True
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._animate_icon, daemon=True).start()
        if self.show_notifications:
            icon.notify(
                f"正在监控 {self.code}，右键图标可查看状态或退出。",
                "AStock MACD 已启动",
            )

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            self._refresh()
            self.stop_event.wait(self.poll_seconds)

    def _animate_icon(self) -> None:
        while not self.stop_event.wait(3):
            with self.state_lock:
                if self.signal_side and time.monotonic() >= self.signal_until:
                    self.signal_side = None
                if not self.signal_side:
                    self.icon_mode = "macd" if self.icon_mode == "price" else "price"
            self._update_icon()

    def _update_icon(self) -> None:
        with self.state_lock:
            signal_side = self.signal_side
            points = list(self.points)
            mode = self.icon_mode
            state = self.market_state
            status = self.status
        if signal_side:
            self.icon.icon = self._create_signal_icon(signal_side)
            marker = "【买入观察】" if signal_side == "BUY" else "【卖出观察】"
            self.icon.title = f"{marker} {status}"
        else:
            self.icon.icon = self._create_market_icon(points, mode, state)
            label = "分时线" if mode == "price" else "MACD"
            self.icon.title = f"{status} | {label}"
        self.icon.update_menu()

    def _refresh(self) -> None:
        if not self.check_lock.acquire(blocking=False):
            return
        try:
            with self.state_lock:
                code = self.code
            name, bars = fetch_minute_bars(code, days=1)
            points = calculate_macd(completed_bars(bars))
            if not points:
                raise RuntimeError("当前没有已完成的分钟行情")
            latest = points[-1]
            signal = latest_macd_signal(points)
            is_new_bar = latest.time != self.last_processed
            state = "bull" if latest.histogram > 0 else "bear"
            state_text = "MACD 多头" if state == "bull" else "MACD 空头"
            with self.state_lock:
                if code != self.code:
                    return
                self.name = name
                self.status = (
                    f"{name} {latest.close:.2f} | {latest.time:%H:%M} {state_text}"
                )
                self.last_error = None
                latest_date = latest.time.date()
                self.points = [
                    point for point in points if point.time.date() == latest_date
                ]
                self.market_state = state
            self._update_icon()
            if (
                is_new_bar
                and signal is not None
                and signal.time.date() == datetime.now(CHINA_TZ).date()
            ):
                self._notify_signal(signal)
            self.last_processed = latest.time
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
            self.status = f"{self.code} | 行情异常"
            self.points = []
            self.market_state = "neutral"
            self._update_icon()
            if self.show_notifications and error != self.last_error:
                self.icon.notify(error, "AStock 行情获取失败")
            self.last_error = error
        finally:
            self.check_lock.release()

    def _notify_signal(self, signal: MacdSignal) -> None:
        action = "买入观察（金叉）" if signal.side == "BUY" else "卖出观察（死叉）"
        message = (
            f"{self.name} {self.code}  {signal.price:.2f}\n"
            f"{signal.time:%H:%M}  DIF {signal.dif:.4f}  DEA {signal.dea:.4f}"
        )
        with self.state_lock:
            self.signal_side = signal.side
            self.signal_until = time.monotonic() + 30
            self.last_signal = (
                f"最近信号：{action} {signal.time:%H:%M} {signal.price:.2f}"
            )
        self._update_icon()
        print(
            f"[{datetime.now(CHINA_TZ):%H:%M:%S}] {action} | {message.replace(chr(10), ' | ')}",
            flush=True,
        )
        if self.show_notifications:
            if sys.platform == "win32":
                ctypes.windll.user32.MessageBeep(
                    0x00000040 if signal.side == "BUY" else 0x00000030
                )
            self.icon.notify(message, f"MACD {action}")

    def _check_now(self, _icon, _item) -> None:
        threading.Thread(target=self._refresh, daemon=True).start()

    def _change_stock(self, _icon, _item) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            raw_code = simpledialog.askstring(
                "更换股票", "请输入 6 位股票代码：", initialvalue=self.code, parent=root
            )
            if not raw_code:
                return
            code = normalize_stock_code(raw_code)
            with self.state_lock:
                self.code = code
                self.name = code
                self.status = f"{code} | 正在获取行情"
                self.last_processed = None
                self.last_error = None
                self.points = []
                self.market_state = "neutral"
                self.signal_side = None
                self.last_signal = "最近信号：暂无"
            self._update_icon()
            threading.Thread(target=self._refresh, daemon=True).start()
        except ValueError as exc:
            if self.show_notifications:
                self.icon.notify(str(exc), "股票代码无效")
        finally:
            root.destroy()

    def _quit(self, icon, _item) -> None:
        self.stop_event.set()
        icon.stop()


def choose_stock_code(code: str | None) -> str | None:
    if code:
        return code
    root = tk.Tk()
    root.withdraw()
    try:
        return simpledialog.askstring(
            "选择个股", "请输入 6 位股票代码（如 600519）：", parent=root
        )
    finally:
        root.destroy()


def run_tray(code: str | None, poll_seconds: int, show_notifications: bool) -> int:
    raw_code = choose_stock_code(code)
    if not raw_code:
        return 2
    try:
        return MacdTray(raw_code, poll_seconds, show_notifications).run()
    except (ValueError, RuntimeError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        show_popup("AStock MACD 启动失败", str(exc), warning=True)
        return 2


TaskbarDataMessage = tuple[
    Literal["data"],
    str,
    str,
    list[MinuteBar],
    list[MacdPoint],
    MacdSignal | None,
    IntradayContext | None,
    list[StockBoard],
    StockBoard | None,
    float | None,
    OrderBook | None,
    bool,
]
TaskbarErrorMessage = tuple[Literal["error"], str, str]
TaskbarBacktestMessage = tuple[Literal["backtest"], str, str, BacktestResult]
TaskbarBacktestErrorMessage = tuple[Literal["backtest_error"], str, str]
TaskbarMessage = (
    TaskbarDataMessage
    | TaskbarErrorMessage
    | TaskbarBacktestMessage
    | TaskbarBacktestErrorMessage
)


class WindowRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


# 价量状态判定阈值：最新成交量相对前 5 根分钟均量的倍数。
VOLUME_SURGE_RATIO = 1.3
VOLUME_SHRINK_RATIO = 0.7


@dataclass(frozen=True)
class PriceVolumeState:
    """单根分钟线的价量体征，供信号分类与语音播报共享。"""

    ratio: float
    volume_state: str
    price_up: bool
    histogram_growing: bool

    @property
    def direction(self) -> str:
        return "上涨" if self.price_up else "下跌"

    @property
    def momentum(self) -> str:
        return "MACD 柱变长" if self.histogram_growing else "MACD 柱变短"


class MacdTaskbarBar(DesktopSettingsMixin):
    WIDTH = 565
    HEIGHT = 32
    CHART_WIDTH = 88
    ORDER_BOOK_WIDTH = 155

    def __init__(self, code: str | None, poll_seconds: int, config_path: Path | None = None) -> None:
        self._init_settings(config_path, poll_seconds)
        self.code = normalize_stock_code(code or "000001")
        self.poll_seconds = poll_seconds
        self.root = tk.Tk()
        self.root.title("AStock 行情条")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        if sys.platform == "win32":
            self.root.attributes("-toolwindow", True)
        self.taskbar_color = "#20272d"
        self.foreground_color = "#f3f4f6"
        self.root.configure(bg=self.taskbar_color)
        self.root.resizable(False, False)
        self.messages: queue.Queue[TaskbarMessage] = queue.Queue()
        self.stop_event = threading.Event()
        self.refresh_event = threading.Event()
        self.state_lock = threading.Lock()
        self.last_processed: datetime | None = None
        self.latest_point: MacdPoint | None = None
        self.latest_bars: list[MinuteBar] = []
        self.latest_points: list[MacdPoint] = []
        self.last_price_volume_signal: str | None = None
        self.latest_change: float | None = None
        self.latest_order_book: OrderBook | None = None
        self.order_book_enabled = False
        self.order_book_window: tk.Toplevel | None = None
        self.order_book_rows: dict[
            tuple[str, int], tuple[tk.Label, tk.Label]
        ] = {}
        self.order_book_title_label: tk.Label | None = None
        self.order_book_seal_label: tk.Label | None = None
        self.order_book_last_label: tk.Label | None = None
        self.context_snapshot: IntradayContext | None = None
        self.global_context: GlobalMarketContext | None = None
        self.context_code: str | None = None
        self.context_updated = 0.0
        self.equity_event: EquityEvent | None = None
        self.equity_updated = 0.0
        self.available_boards: list[StockBoard] = []
        self.selected_board: StockBoard | None = None
        self.board_menu_signature: tuple[tuple[str, str, str], ...] = ()
        self.candidate_mode = self.desktop_settings["candidateMode"]
        self.strategy_config = strategy_config(self.desktop_settings)
        self.trade_journal = TradeJournal(user_data_dir() / "intraday_trades.json")
        self.backtest_running = False
        self.alert_generation = 0
        self.drag_origin: tuple[int, int] | None = None
        self.last_taskbar_rect: tuple[int, int, int, int] | None = None
        self._build()
        self._dock_inside_taskbar()
        if not self._display_allowed(datetime.now(CHINA_TZ)):
            self.root.withdraw()
        self._start_settings_watch()

    def _build(self) -> None:
        self.root.grid_columnconfigure(2, weight=1)
        self.root.grid_columnconfigure(3, weight=1)
        self.root.grid_columnconfigure(4, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.signal_strip = tk.Frame(self.root, width=3, bg="#555b66")
        self.signal_strip.grid(row=0, column=0, sticky="ns")

        self.info = tk.Frame(self.root, width=125, bg=self.taskbar_color, padx=2)
        self.info.grid(row=0, column=1, sticky="nsew")
        self.info.grid_propagate(False)
        self.info.pack_propagate(False)
        self.top_info = tk.Frame(
            self.info, height=18, bg=self.taskbar_color
        )
        self.top_info.pack(side="top", fill="x")
        self.top_info.pack_propagate(False)
        self.code_var = tk.StringVar(value=self.code)
        self.code_entry = tk.Entry(
            self.top_info,
            textvariable=self.code_var,
            width=6,
            bg=self.taskbar_color,
            fg="#f3f4f6",
            insertbackground="#f3f4f6",
            relief="flat",
            font=("Consolas", 7, "bold"),
        )
        self.code_entry.pack(side="left", ipady=1, padx=(0, 2))
        self.code_entry.bind("<Return>", self._change_code)
        self.price_label = tk.Label(
            self.top_info,
            text="--",
            bg=self.taskbar_color,
            fg="#f3f4f6",
            anchor="e",
            font=("Microsoft YaHei UI", 7, "bold"),
        )
        self.price_label.pack(side="right", fill="x", expand=True, padx=(2, 0))
        self.context_row = tk.Frame(
            self.info,
            height=12,
            bg=self.taskbar_color,
        )
        self.context_row.pack(side="bottom", fill="x")
        self.context_row.pack_propagate(False)
        self.market_context_label = tk.Label(
            self.context_row,
            text="盘--",
            bg=self.taskbar_color,
            fg="#929aa8",
            anchor="w",
            font=("Microsoft YaHei UI", 6),
        )
        self.market_context_label.pack(side="left")
        self.sector_context_label = tk.Label(
            self.context_row,
            text="板--",
            bg=self.taskbar_color,
            fg="#929aa8",
            anchor="e",
            font=("Microsoft YaHei UI", 6),
        )
        self.sector_context_label.pack(side="right")

        self.price_canvas = tk.Canvas(
            self.root,
            width=self.CHART_WIDTH,
            height=self.HEIGHT,
            bg=self.taskbar_color,
            highlightthickness=0,
        )
        self.price_canvas.grid(row=0, column=2, sticky="nsew")
        self.macd_canvas = tk.Canvas(
            self.root,
            width=self.CHART_WIDTH,
            height=self.HEIGHT,
            bg=self.taskbar_color,
            highlightthickness=0,
        )
        self.macd_canvas.grid(row=0, column=3, sticky="nsew")
        self.volume_canvas = tk.Canvas(
            self.root,
            width=self.CHART_WIDTH,
            height=self.HEIGHT,
            bg=self.taskbar_color,
            highlightthickness=0,
        )
        self.volume_canvas.grid(row=0, column=4, sticky="nsew")

        self.order_book_strip = tk.Frame(
            self.root,
            width=self.ORDER_BOOK_WIDTH,
            height=self.HEIGHT,
            bg=self.taskbar_color,
            padx=3,
        )
        self.order_book_strip.grid(row=0, column=5, sticky="nsew")
        self.order_book_strip.grid_propagate(False)
        self.order_book_strip.pack_propagate(False)
        self.bid_lots_label = tk.Label(
            self.order_book_strip,
            text="买  --  --  --  --  --",
            bg=self.taskbar_color,
            fg="#e45b65",
            anchor="w",
            font=("Microsoft YaHei UI", 6),
        )
        self.bid_lots_label.place(x=3, y=0, width=self.ORDER_BOOK_WIDTH - 6, height=16)
        self.ask_lots_label = tk.Label(
            self.order_book_strip,
            text="卖  --  --  --  --  --",
            bg=self.taskbar_color,
            fg="#36c98f",
            anchor="w",
            font=("Microsoft YaHei UI", 6),
        )
        self.ask_lots_label.place(x=3, y=16, width=self.ORDER_BOOK_WIDTH - 6, height=16)
        self.close_button = tk.Button(
            self.root,
            text="×",
            command=self.close,
            width=1,
            bg=self.taskbar_color,
            fg="#929aa8",
            activebackground="#e45b65",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )
        self.close_button.grid(row=0, column=6, sticky="ns")
        self.context_menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=self.taskbar_color,
            fg="#f3f4f6",
            activebackground="#4a5562",
            activeforeground="#ffffff",
        )
        self.context_menu.add_command(
            label="测试买入提醒", command=lambda: self._test_signal("BUY")
        )
        self.context_menu.add_command(
            label="测试卖出提醒", command=lambda: self._test_signal("SELL")
        )
        self.order_book_var = tk.BooleanVar(value=False)
        self.context_menu.add_checkbutton(
            label="买卖五档",
            variable=self.order_book_var,
            command=self._toggle_order_book,
        )
        self.context_menu.add_command(
            label="历史回测（日内做T）", command=self._start_backtest
        )
        backtest_index = self.context_menu.index("end")
        if backtest_index is None:
            raise RuntimeError("无法创建历史回测菜单")
        self.backtest_menu_index = backtest_index
        self.context_menu.add_command(label="环境：正在获取", state="disabled")
        context_index = self.context_menu.index("end")
        if context_index is None:
            raise RuntimeError("无法创建市场环境菜单")
        self.context_menu_index = context_index
        self.context_menu.add_command(label="股权事件：正在获取", state="disabled")
        equity_index = self.context_menu.index("end")
        if equity_index is None:
            raise RuntimeError("无法创建股权事件菜单")
        self.equity_menu_index = equity_index
        self.context_menu.add_command(label="最近卖出：暂无", state="disabled")
        recent_sell_index = self.context_menu.index("end")
        if recent_sell_index is None:
            raise RuntimeError("无法创建最近卖出菜单")
        self.recent_sell_menu_index = recent_sell_index
        self.context_menu.add_command(label="最近买回：暂无", state="disabled")
        recent_buy_index = self.context_menu.index("end")
        if recent_buy_index is None:
            raise RuntimeError("无法创建最近买回菜单")
        self.recent_buy_menu_index = recent_buy_index
        self.context_menu.add_command(label="实盘状态：未记录卖出", state="disabled")
        actual_status_index = self.context_menu.index("end")
        if actual_status_index is None:
            raise RuntimeError("无法创建实盘状态菜单")
        self.actual_status_menu_index = actual_status_index
        self.context_menu.add_command(
            label="记录实际卖出1成", command=lambda: self._record_actual_trade("SELL")
        )
        actual_sell_index = self.context_menu.index("end")
        if actual_sell_index is None:
            raise RuntimeError("无法创建实际卖出菜单")
        self.actual_sell_menu_index = actual_sell_index
        self.context_menu.add_command(
            label="记录实际买回1成", command=lambda: self._record_actual_trade("BUY")
        )
        actual_buy_index = self.context_menu.index("end")
        if actual_buy_index is None:
            raise RuntimeError("无法创建实际买回菜单")
        self.actual_buy_menu_index = actual_buy_index
        self.context_menu.add_command(
            label="情绪与指标说明", command=self._show_insight_window
        )
        self.context_menu.add_separator()
        self.board_code_var = tk.StringVar(value="")
        self.board_menu = tk.Menu(self.context_menu, tearoff=False)
        self.context_menu.add_cascade(label="对照板块", menu=self.board_menu)
        self.candidate_mode_var = tk.StringVar(value=self.candidate_mode)
        formula_menu = tk.Menu(self.context_menu, tearoff=False)
        for label, value in (
            ("柱缩反T（推荐）：同色连续缩短3次", "histogram"),
            ("灵敏反T：单根交叉", "sensitive"),
            ("平衡反T（推荐）：双根 + VWAP", "balanced"),
            ("稳健反T：三根 + VWAP/EMA", "steady"),
        ):
            formula_menu.add_radiobutton(
                label=label,
                variable=self.candidate_mode_var,
                value=value,
                command=self._change_candidate_mode,
            )
        self.context_menu.add_cascade(label="候选公式", menu=formula_menu)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="退出", command=self.close)
        for widget in (
            self.root,
            self.info,
            self.top_info,
            self.code_entry,
            self.price_label,
            self.context_row,
            self.market_context_label,
            self.sector_context_label,
            self.price_canvas,
            self.macd_canvas,
            self.volume_canvas,
            self.order_book_strip,
            self.bid_lots_label,
            self.ask_lots_label,
            self.close_button,
        ):
            widget.bind("<Enter>", self._show_fully)
            widget.bind("<Button-3>", self._show_context_menu)
        for widget in (
            self.root,
            self.info,
            self.price_canvas,
            self.macd_canvas,
            self.volume_canvas,
            self.order_book_strip,
            self.bid_lots_label,
            self.ask_lots_label,
        ):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)
            widget.bind("<Double-Button-1>", self._restore_docking)
        self._update_actual_trade_menu()
        self._update_equity_menu()

    def _toggle_order_book(self) -> None:
        enabled = bool(self.order_book_var.get())
        with self.state_lock:
            self.order_book_enabled = enabled
        if enabled:
            self._show_order_book_window()
            self.refresh_event.set()
        else:
            self._destroy_order_book_window()

    def _show_order_book_window(self) -> None:
        window = self.order_book_window
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            self._position_order_book_window()
            return

        window = tk.Toplevel(self.root)
        self.order_book_window = window
        window.title(f"{self.code} 买卖五档")
        window.attributes("-topmost", True)
        if sys.platform == "win32":
            window.attributes("-toolwindow", True)
        window.resizable(False, False)
        window.configure(bg="#17191d")
        window.protocol("WM_DELETE_WINDOW", self._close_order_book_window)

        header = tk.Frame(window, bg="#22252b", height=34)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.order_book_title_label = tk.Label(
            header,
            text=f"{self.code}  正在获取盘口",
            bg="#22252b",
            fg="#f3f4f6",
            anchor="w",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.order_book_title_label.pack(side="left", fill="x", expand=True, padx=8)
        self.order_book_last_label = tk.Label(
            header,
            text="--",
            bg="#22252b",
            fg="#aeb6c2",
            anchor="e",
            font=("Consolas", 9, "bold"),
        )
        self.order_book_last_label.pack(side="right", padx=8)

        self.order_book_seal_label = tk.Label(
            window,
            text="封单：正在获取",
            bg="#17191d",
            fg="#929aa8",
            anchor="w",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.order_book_seal_label.pack(fill="x", padx=8, pady=(5, 3))

        table = tk.Frame(window, bg="#17191d")
        table.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        tk.Label(
            table,
            text="档位",
            bg="#17191d",
            fg="#737b88",
            width=6,
            anchor="w",
            font=("Microsoft YaHei UI", 7),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            table,
            text="价格",
            bg="#17191d",
            fg="#737b88",
            width=12,
            anchor="e",
            font=("Microsoft YaHei UI", 7),
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            table,
            text="数量（手）",
            bg="#17191d",
            fg="#737b88",
            width=14,
            anchor="e",
            font=("Microsoft YaHei UI", 7),
        ).grid(row=0, column=2, sticky="e")

        row = 1
        for side, level, color in (
            [("ASK", level, "#36c98f") for level in range(5, 0, -1)]
            + [("BID", level, "#e45b65") for level in range(1, 6)]
        ):
            if row == 6:
                tk.Frame(table, bg="#343a43", height=1).grid(
                    row=row, column=0, columnspan=3, sticky="ew", pady=2
                )
                row += 1
            side_text = "卖" if side == "ASK" else "买"
            tk.Label(
                table,
                text=f"{side_text}{level}",
                bg="#17191d",
                fg=color,
                width=6,
                anchor="w",
                font=("Microsoft YaHei UI", 8),
            ).grid(row=row, column=0, sticky="w")
            price_label = tk.Label(
                table,
                text="--",
                bg="#17191d",
                fg=color,
                width=12,
                anchor="e",
                font=("Consolas", 8),
            )
            price_label.grid(row=row, column=1, sticky="e")
            volume_label = tk.Label(
                table,
                text="--",
                bg="#17191d",
                fg="#d4d7dc",
                width=14,
                anchor="e",
                font=("Consolas", 8),
            )
            volume_label.grid(row=row, column=2, sticky="e")
            self.order_book_rows[(side, level)] = (price_label, volume_label)
            row += 1

        self._position_order_book_window()
        self._render_order_book(self.latest_order_book)

    def _close_order_book_window(self) -> None:
        self.order_book_var.set(False)
        with self.state_lock:
            self.order_book_enabled = False
        self._destroy_order_book_window()

    def _destroy_order_book_window(self) -> None:
        window = self.order_book_window
        self.order_book_window = None
        self.order_book_rows = {}
        self.order_book_title_label = None
        self.order_book_seal_label = None
        self.order_book_last_label = None
        if window is not None and window.winfo_exists():
            window.destroy()

    def _position_order_book_window(self) -> None:
        window = self.order_book_window
        if window is None or not window.winfo_exists():
            return
        width, height = 250, 312
        self.root.update_idletasks()
        x = self.root.winfo_x() + self.WIDTH - width
        y = self.root.winfo_y() - height - 4
        if y < 0:
            y = self.root.winfo_y() + self.HEIGHT + 4
        x = min(max(x, 0), max(self.root.winfo_screenwidth() - width, 0))
        window.geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _format_lots(volume_lots: int) -> str:
        if volume_lots >= 10_000:
            return f"{volume_lots / 10_000:.2f}万"
        return f"{volume_lots:,}"

    @staticmethod
    def _format_shares(volume_lots: int) -> str:
        shares = volume_lots * 100
        if shares >= 100_000_000:
            return f"{shares / 100_000_000:.2f}亿股"
        if shares >= 10_000:
            return f"{shares / 10_000:.2f}万股"
        return f"{shares:,}股"

    @staticmethod
    def _format_inline_lots(volume_lots: int) -> str:
        if volume_lots >= 100_000:
            return f"{volume_lots / 10_000:.0f}万"
        if volume_lots >= 10_000:
            return f"{volume_lots / 10_000:.1f}万"
        return str(volume_lots)

    def _render_inline_order_book(self, book: OrderBook | None) -> None:
        if book is None or book.code != self.code:
            self.bid_lots_label.config(text="买  --  --  --  --  --")
            self.ask_lots_label.config(text="卖  --  --  --  --  --")
            return

        bids = [self._format_inline_lots(level.volume_lots) for level in book.bids]
        asks = [self._format_inline_lots(level.volume_lots) for level in book.asks]
        if book.sealed_side == "UP":
            bids[0] = f"封{bids[0]}"
        elif book.sealed_side == "DOWN":
            asks[0] = f"封{asks[0]}"
        self.bid_lots_label.config(text=f"买  {'  '.join(bids)}")
        self.ask_lots_label.config(text=f"卖  {'  '.join(asks)}")

    def _render_order_book(self, book: OrderBook | None) -> None:
        window = self.order_book_window
        if window is None or not window.winfo_exists():
            return
        if book is None or book.code != self.code:
            if self.order_book_title_label is not None:
                self.order_book_title_label.config(text=f"{self.code}  正在获取盘口")
            if self.order_book_last_label is not None:
                self.order_book_last_label.config(text="--", fg="#929aa8")
            if self.order_book_seal_label is not None:
                self.order_book_seal_label.config(text="封单：正在获取", fg="#929aa8")
            for price_label, volume_label in self.order_book_rows.values():
                price_label.config(text="--")
                volume_label.config(text="--")
            return

        window.title(f"{book.code} {book.name} - 买卖五档")
        quote_time = book.quote_time.strftime("%H:%M:%S") if book.quote_time else "--"
        if self.order_book_title_label is not None:
            self.order_book_title_label.config(text=f"{book.code}  {book.name}  {quote_time}")
        if self.order_book_last_label is not None:
            self.order_book_last_label.config(text=f"{book.last_price:.2f}")
        for level, item in enumerate(book.asks, 1):
            labels = self.order_book_rows.get(("ASK", level))
            if labels is not None:
                labels[0].config(text=f"{item.price:.2f}" if item.price else "--")
                labels[1].config(text=self._format_lots(item.volume_lots))
        for level, item in enumerate(book.bids, 1):
            labels = self.order_book_rows.get(("BID", level))
            if labels is not None:
                labels[0].config(text=f"{item.price:.2f}" if item.price else "--")
                labels[1].config(text=self._format_lots(item.volume_lots))

        if self.order_book_seal_label is not None:
            if book.sealed_side == "UP":
                text = (
                    f"涨停封单：{self._format_lots(book.sealed_volume_lots)}手 / "
                    f"{self._format_shares(book.sealed_volume_lots)}"
                )
                self.order_book_seal_label.config(text=text, fg="#e45b65")
            elif book.sealed_side == "DOWN":
                text = (
                    f"跌停封单：{self._format_lots(book.sealed_volume_lots)}手 / "
                    f"{self._format_shares(book.sealed_volume_lots)}"
                )
                self.order_book_seal_label.config(text=text, fg="#36c98f")
            else:
                self.order_book_seal_label.config(
                    text="封单：当前未封涨停或跌停", fg="#929aa8"
                )

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        if not hwnd:
            return None
        rect = WindowRect()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def _taskbar_bounds(
        self,
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None] | None:
        if sys.platform != "win32":
            return None
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowExW.restype = ctypes.c_void_p
        user32.FindWindowExW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
        ]
        taskbar = user32.FindWindowW("Shell_TrayWnd", None)
        taskbar_handle = int(taskbar or 0)
        taskbar_rect = self._window_rect(taskbar_handle)
        if taskbar_rect is None:
            return None
        notification = user32.FindWindowExW(taskbar_handle, 0, "TrayNotifyWnd", None)
        return taskbar_rect, self._window_rect(int(notification or 0))

    @staticmethod
    def _sample_taskbar_color(rect: tuple[int, int, int, int]) -> str | None:
        try:
            image = ImageGrab.grab(bbox=rect).convert("RGB")
            quantized = image.quantize(colors=8)
            colors = quantized.getcolors()
            palette = quantized.getpalette()
            if not colors or not palette:
                return None
            raw_index = max(colors, key=lambda item: item[0])[1]
            if not isinstance(raw_index, int):
                return None
            index = raw_index
            red, green, blue = palette[index * 3 : index * 3 + 3]
            return f"#{red:02x}{green:02x}{blue:02x}"
        except (OSError, ValueError):
            return None

    def _apply_taskbar_color(self, color: str) -> None:
        self.taskbar_color = color
        self.foreground_color = foreground_color(color)
        for widget in (
            self.root,
            self.info,
            self.top_info,
            self.code_entry,
            self.price_label,
            self.context_row,
            self.market_context_label,
            self.sector_context_label,
            self.price_canvas,
            self.macd_canvas,
            self.volume_canvas,
            self.order_book_strip,
            self.bid_lots_label,
            self.ask_lots_label,
            self.close_button,
        ):
            widget.configure(bg=color)
        self.close_button.configure(activebackground="#e45b65")
        self.code_entry.configure(fg=self.foreground_color, insertbackground=self.foreground_color)
        self.close_button.configure(fg=self.foreground_color)
        self.context_menu.configure(bg=color, fg=self.foreground_color)

    def _refresh_chameleon(self) -> None:
        if not self.desktop_settings["chameleon"]:
            if self.taskbar_color != "#20272d":
                self._apply_taskbar_color("#20272d")
            return
        if self.drag_origin is not None or not self.root.winfo_viewable():
            return
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        monitor = self._taskbar_monitor((x, y, x + width, y + height))
        left, top = max(monitor[0], x - 8), max(monitor[1], y - 8)
        right, bottom = min(monitor[2], x + width + 8), min(monitor[3], y + height + 8)
        if right <= left or bottom <= top:
            return
        try:
            image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            color = background_color(image, (x - left, y - top, x + width - left, y + height - top))
            if color and color != self.taskbar_color:
                self._apply_taskbar_color(color)
        except (OSError, ValueError):
            pass

    def _dock_inside_taskbar(self) -> None:
        if self.drag_origin is not None:
            return
        self.root.update_idletasks()
        self.root.deiconify()
        settings = self.desktop_settings
        self.root.attributes("-topmost", settings["topmost"])
        bounds = self._taskbar_bounds()
        if bounds is not None:
            taskbar, notification = bounds
            monitor = self._taskbar_monitor(taskbar)
            x, y = calculate_taskbar_position(
                taskbar, notification, monitor, self.WIDTH, self.HEIGHT, settings["dockMode"]
            )
            if taskbar != self.last_taskbar_rect:
                self.last_taskbar_rect = taskbar
        else:
            x = self.root.winfo_screenwidth() - self.WIDTH - 8
            y = self.root.winfo_screenheight() - self.HEIGHT - 48
        if settings["dockMode"] == "floating":
            x, y = settings["windowX"], settings["windowY"]
            ml, mt, mr, mb = self._taskbar_monitor((x, y, x + self.WIDTH, y + self.HEIGHT))
            x = min(max(x, ml), max(ml, mr - self.WIDTH))
            y = min(max(y, mt), max(mt, mb - self.HEIGHT))
        # Tk interprets negative offsets relative to the right/bottom screen edge.
        # Use native coordinates below so monitors left of the primary work too.
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        if sys.platform != "win32":
            self.root.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.root.update_idletasks()
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            user32.GetParent.restype = ctypes.c_void_p
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.SetWindowPos.restype = ctypes.c_bool
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            child = self.root.winfo_id()
            wrapper = int(user32.GetParent(child) or child)
            user32.SetWindowPos(
                wrapper,
                ctypes.c_void_p(-1 if settings["topmost"] else -2),
                x,
                y,
                self.WIDTH,
                self.HEIGHT,
                0x0050,  # no activation; apply computed position and size, show window
            )
        self._position_order_book_window()
        self._refresh_chameleon()

    def _taskbar_monitor(self, taskbar: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("monitor", WindowRect),
                        ("work", WindowRect), ("flags", ctypes.c_ulong)]

        user32 = ctypes.windll.user32
        user32.MonitorFromRect.argtypes = [ctypes.POINTER(WindowRect), ctypes.c_ulong]
        user32.MonitorFromRect.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = ctypes.c_bool
        rectangle = WindowRect(*taskbar)
        monitor = user32.MonitorFromRect(ctypes.byref(rectangle), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.monitor
            return r.left, r.top, r.right, r.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _maintain_taskbar_position(self) -> None:
        """任务栏压顶靠每 3 秒重新置顶维持，任何异常都不能掐断这条链。"""
        try:
            if not self.root.winfo_exists():
                return
            if self._display_allowed(datetime.now(CHINA_TZ)):
                self._dock_inside_taskbar()
                if self.order_book_enabled and self.order_book_window is not None:
                    self.order_book_window.deiconify()
            else:
                self.root.withdraw()
                if self.order_book_window is not None:
                    self.order_book_window.withdraw()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 任务栏保活异常，下轮自动重试：{exc}", file=sys.stderr)
            try:
                self._set_context_labels("保活重试", "", "#e5b94f", "#e5b94f")
            except Exception:  # noqa: BLE001
                pass
        try:
            if self.root.winfo_exists():
                self.root.after(3000, self._maintain_taskbar_position)
        except tk.TclError:
            pass

    @staticmethod
    def _is_display_time(now: datetime) -> bool:
        current = now.time()
        return datetime_time(9, 0) <= current < datetime_time(15, 30)

    def _display_allowed(self, now: datetime) -> bool:
        return display_allowed(self.desktop_settings, now)

    def run(self) -> int:
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._consume_messages)
        self.root.after(3000, self._maintain_taskbar_position)
        self.root.mainloop()
        return 0

    def close(self) -> None:
        self.stop_event.set()
        self.refresh_event.set()
        self.root.destroy()

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            with self.state_lock:
                code = self.code
                candidate_mode = self.candidate_mode
                selected_board = self.selected_board
            try:
                # 实时行情使用短超时，避免某个行情源卡住时拖慢整轮刷新。
                name, bars = fetch_minute_bars(code, days=1, timeout=self.desktop_settings["quoteTimeout"])
                closed_bars = completed_bars(bars)
                points = calculate_macd(closed_bars)
                if not points:
                    raise RuntimeError("当前没有已完成的分钟行情")
                latest_date = points[-1].time.date()
                today_bars = [
                    bar for bar in closed_bars if bar.time.date() == latest_date
                ]
                today_points = [
                    point for point in points if point.time.date() == latest_date
                ]
                candidates = self._reverse_t_actions(
                    today_bars,
                    today_points,
                    code=code,
                    mode=candidate_mode,
                )
                raw_candidates = candidate_sides(
                    today_bars, today_points, candidate_mode
                )
                pending_actual = self.trade_journal.open_sell(
                    code, today_points[-1].time.date()
                )
                guarded_signal = candidates[-1] is not None
                signal_side = candidates[-1]
                # 价格门槛未满足时仍提示红柱候选，但明确标注“观察”，不能误解为
                # 已满足卖出条件；已有真实卖出待买回时不再产生新的卖出提醒。
                if (
                    signal_side is None
                    and pending_actual is None
                    and self.strategy_config.sell_start <= today_points[-1].time.strftime("%H:%M") <= self.strategy_config.sell_cutoff
                    and raw_candidates[-1] in ("SELL", "BUY")
                ):
                    signal_side = raw_candidates[-1]
                    guarded_signal = False
                context = (
                    self.context_snapshot if self.context_code == code else None
                )
                boards = list(self.available_boards) if self.context_code == code else []
                if context is None or time.monotonic() - self.context_updated >= self.desktop_settings["contextRefresh"]:
                    try:
                        boards = fetch_stock_boards(code, timeout=self.desktop_settings["contextTimeout"])
                        with self.state_lock:
                            latest_selected_board = self.selected_board
                        matching_board = next(
                            (
                                board
                                for board in boards
                                if latest_selected_board is not None
                                and board.code == latest_selected_board.code
                            ),
                            None,
                        )
                        selected_board = matching_board or next((board for board in boards if board.code == self.desktop_settings["sectorCode"]), None) or default_stock_board(boards)
                        context = fetch_intraday_context(
                            code,
                            timeout=self.desktop_settings["contextTimeout"],
                            sector=selected_board,
                            boards=boards,
                        )
                        with self.state_lock:
                            # A board change can happen while the network request is
                            # in flight. Never let that older request overwrite it.
                            current_selected_board = next(
                                (
                                    board
                                    for board in boards
                                    if self.selected_board is not None
                                    and board.code == self.selected_board.code
                                ),
                                None,
                            )
                            if (
                                current_selected_board is not None
                                and current_selected_board.code != selected_board.code
                            ):
                                selected_board = current_selected_board
                                context = None
                                self.context_snapshot = None
                                self.context_code = None
                            else:
                                self.context_snapshot = context
                                self.context_code = code
                            self.available_boards = boards
                            self.selected_board = selected_board
                    except (OSError, RuntimeError, ValueError):
                        pass
                    self.context_updated = (
                        time.monotonic() if context is not None else 0.0
                    )
                    try:
                        self.global_context = fetch_global_market_context(
                            timeout=self.desktop_settings["contextTimeout"]
                        )
                    except (OSError, RuntimeError, ValueError):
                        self.global_context = None
                if (
                    self.context_code != code
                    or time.monotonic() - self.equity_updated >= self.desktop_settings["equityRefresh"]
                ):
                    try:
                        equity_event = fetch_equity_event(code, timeout=self.desktop_settings["contextTimeout"])
                        with self.state_lock:
                            self.equity_event = equity_event
                    except (OSError, RuntimeError, ValueError):
                        with self.state_lock:
                            self.equity_event = None
                    self.equity_updated = time.monotonic()
                stock_change = None
                try:
                    stock_change = fetch_tencent_stock_change(code, timeout=self.desktop_settings["contextTimeout"])
                except (OSError, RuntimeError, ValueError):
                    pass
                order_book = None
                try:
                    order_book = fetch_order_book(code, timeout=self.desktop_settings["quoteTimeout"])
                except (OSError, RuntimeError, ValueError):
                    pass
                side = signal_side
                candidate = (
                    MacdSignal(
                        time=today_points[-1].time,
                        side=side,
                        price=today_points[-1].close,
                        dif=today_points[-1].dif,
                        dea=today_points[-1].dea,
                        histogram=today_points[-1].histogram,
                    )
                    if side is not None
                    else None
                )
                self.messages.put(
                    (
                        "data",
                        code,
                        name,
                        today_bars,
                        today_points,
                        candidate,
                        context,
                        boards,
                        selected_board,
                        stock_change,
                        order_book,
                        guarded_signal,
                        self.global_context,
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                self.messages.put(("error", code, str(exc)))
            self.refresh_event.wait(self.poll_seconds)
            self.refresh_event.clear()

    def _consume_messages_once(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == "backtest":
                    _, code, mode, result = message
                    self.backtest_running = False
                    self.context_menu.entryconfigure(
                        self.backtest_menu_index,
                        label="历史回测（日内做T）",
                        state="normal",
                    )
                    self._show_backtest(code, mode, result)
                    continue
                if message[0] == "backtest_error":
                    _, code, error = message
                    self.backtest_running = False
                    self.context_menu.entryconfigure(
                        self.backtest_menu_index,
                        label="历史回测（日内做T）",
                        state="normal",
                    )
                    show_popup(f"{code} 历史回测失败", error, warning=True)
                    continue
                if message[0] == "error":
                    _, code, error = message
                    if code == self.code:
                        self._set_context_labels(
                            "获取失败", "", "#e45b65", "#e45b65"
                        )
                        self.price_label.config(text="失败", fg="#e45b65")
                        self.code_entry.config(fg="#e45b65")
                    continue
                (
                    _,
                    code,
                    _name,
                    bars,
                    points,
                    signal,
                    context,
                    boards,
                    selected_board,
                    stock_change,
                    order_book,
                    guarded_signal,
                    global_context,
                ) = message
                if code != self.code:
                    continue
                latest = points[-1]
                self.latest_point = latest
                self.latest_bars = bars
                self.latest_points = points
                try:
                    price_volume_signal = self._price_volume_signal()
                except (IndexError, ZeroDivisionError):
                    price_volume_signal = None
                self.latest_change = stock_change
                if order_book is not None:
                    self.latest_order_book = order_book
                    self._render_inline_order_book(order_book)
                if self.order_book_enabled:
                    self._render_order_book(order_book)
                is_new_bar = latest.time != self.last_processed
                previous_close = points[-2].close if len(points) > 1 else latest.close
                price_color = self._market_color(previous_close, latest.close)
                quote_color = (
                    self._change_color(stock_change)
                    if stock_change is not None
                    else price_color
                )
                quote_text = (
                    f"{latest.close:.2f} {stock_change:+.1%}"
                    if stock_change is not None
                    else f"{latest.close:.2f}"
                )
                self.price_label.config(text=quote_text, fg=quote_color)
                self.code_entry.config(fg=self.foreground_color)
                # Ignore a response queued before the user changed the board.
                # Otherwise the menu can briefly (or permanently) jump back to
                # the old selection while the worker is still refreshing.
                with self.state_lock:
                    current_selected_board = next(
                        (
                            board
                            for board in boards
                            if self.selected_board is not None
                            and board.code == self.selected_board.code
                        ),
                        None,
                    )
                    stale_board_response = (
                        current_selected_board is not None
                        and selected_board is not None
                        and current_selected_board.code != selected_board.code
                    )
                    if stale_board_response:
                        selected_board = current_selected_board
                        self.context_snapshot = None
                        self.context_code = None
                        self.context_updated = 0.0
                    else:
                        self.context_snapshot = context
                if context is None:
                    self._set_context_labels("盘--", "板--", "#929aa8", "#929aa8")
                    menu_text = "环境：暂时无法获取"
                else:
                    self.global_context = global_context
                    combined_score = context.market_change * 0.45 + context.sector_change * 0.20
                    if global_context is not None:
                        combined_score += global_context.risk_score * 0.35
                    regime = "强" if combined_score >= 0.004 else ("弱" if combined_score <= -0.004 else "震")
                    macro = (
                        f"银{global_context.bank:+.1%} 油{global_context.brent:+.1%}"
                        if global_context is not None
                        else "银-- 油--"
                    )
                    self._set_context_labels(
                        f"盘{context.market_change:+.1%} {regime}",
                        macro,
                        self._change_color(context.market_change),
                        self._change_color(global_context.bank if global_context else 0),
                    )
                    menu_text = (
                        f"{context.market_name} {context.market_change:+.2%} / "
                        f"{context.sector_name} {context.sector_change:+.2%}"
                    )
                    if global_context:
                        menu_text += (
                            f" / 美股 {global_context.label}"
                            f" 标普{global_context.sp500:+.2%} 纳指{global_context.nasdaq:+.2%}"
                            f" SOXX{global_context.semis:+.2%} VIX{global_context.vix:+.2%}"
                            f" Brent{global_context.brent:+.2%} 银行{global_context.bank:+.2%}"
                        )
                self.context_menu.entryconfigure(
                    self.context_menu_index, label=menu_text
                )
                self._update_board_menu(boards, selected_board)
                actions = self._reverse_t_actions(bars, points)
                self._update_recent_action_menu(points, actions)
                self._update_actual_trade_menu()
                self._update_equity_menu()
                self._draw_price(bars, points, actions)
                self._draw_macd(points)
                self._draw_volume(bars, points)
                if (
                    is_new_bar
                    and signal is not None
                    and self._alerts_allowed(datetime.now(CHINA_TZ))
                    and signal.time.date() == datetime.now(CHINA_TZ).date()
                ):
                    self._show_signal(signal, guarded=guarded_signal)
                if (
                    price_volume_signal is not None
                    and self.desktop_settings["notifications"]
                    and self.desktop_settings["volumeSpeech"]
                    and self._alerts_allowed(datetime.now(CHINA_TZ))
                    and price_volume_signal[0] != "normal"
                    and price_volume_signal[0] != self.last_price_volume_signal
                    and self._allow_audio(price_volume_signal[0])
                ):
                    threading.Thread(
                        target=_speak_text,
                        args=(f"价量提示，{price_volume_signal[1]}",),
                        daemon=True,
                    ).start()
                    self.last_price_volume_signal = price_volume_signal[0]
                self.last_processed = latest.time
        except queue.Empty:
            pass

    def _consume_messages(self) -> None:
        """薄包装：渲染异常只跳过本轮，绝不打断 150ms 重调度。"""
        try:
            self._consume_messages_once()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 行情条渲染异常，已跳过本轮：{exc}", file=sys.stderr)
        try:
            if self.root.winfo_exists():
                self.root.after(150, self._consume_messages)
        except tk.TclError:
            pass

    def _change_code(self, _event: object | None = None) -> None:
        try:
            code = normalize_stock_code(self.code_var.get())
        except ValueError:
            self._set_context_labels("代码错误", "", "#e45b65", "#e45b65")
            self.price_label.config(text="代码错", fg="#e45b65")
            self.code_entry.focus_set()
            self.code_entry.selection_range(0, tk.END)
            return
        with self.state_lock:
            self.code = code
            self.last_processed = None
            self.last_price_volume_signal = None
            self.alert_generation += 1
            self.context_snapshot = None
            self.context_code = None
            self.context_updated = 0.0
            self.equity_event = None
            self.equity_updated = 0.0
            self.available_boards = []
            self.selected_board = None
            self.board_menu_signature = ()
        self.latest_order_book = None
        self._render_inline_order_book(None)
        self.code_var.set(code)
        self.price_label.config(text="...", fg="#929aa8")
        self._set_context_labels("正在获取", code, "#929aa8", "#929aa8")
        self.price_canvas.delete("all")
        self.macd_canvas.delete("all")
        self.volume_canvas.delete("all")
        self.context_menu.entryconfigure(
            self.recent_sell_menu_index, label="最近卖出：暂无"
        )
        self.context_menu.entryconfigure(
            self.recent_buy_menu_index, label="最近买回：暂无"
        )
        self._update_actual_trade_menu()
        self._update_equity_menu()
        if self.order_book_enabled:
            self._render_order_book(None)
        self.refresh_event.set()

    def _record_actual_trade(self, side: str) -> None:
        point = self.latest_point
        initial_price = point.close if point is not None else 0.0
        action = "卖出" if side == "SELL" else "买回"
        price = simpledialog.askfloat(
            f"记录实际{action}1成",
            f"请输入 {self.code} 的实际成交价格：",
            initialvalue=initial_price,
            minvalue=0.001,
            parent=self.root,
        )
        if price is None:
            return
        try:
            self.trade_journal.record(self.code, side, price)
        except (OSError, ValueError) as exc:
            show_popup(f"实际{action}记录失败", str(exc), warning=True)
            return
        self.last_processed = None
        self._update_actual_trade_menu()
        color = "#36c98f" if side == "SELL" else "#e45b65"
        self._set_context_labels(
            f"已记{action}1成", f"成交 {price:.2f}", color, color
        )
        self.refresh_event.set()

    def _update_actual_trade_menu(self) -> None:
        pending = self.trade_journal.open_sell(self.code)
        completed = self.trade_journal.completed_cycles(self.code)
        latest = self.trade_journal.latest_trade(self.code)
        if pending is None:
            cooling_down = (
                latest is not None
                and latest.side == "BUY"
                and datetime.now(CHINA_TZ) - latest.occurred_at
                < timedelta(minutes=self.strategy_config.cooldown_minutes)
            )
            if completed >= self.strategy_config.max_cycles:
                status = f"实盘状态：今日已完成{completed}轮，停止开新单"
            elif cooling_down:
                status = f"实盘状态：已完成{completed}轮，买回后冷却中"
            else:
                status = f"实盘状态：已完成{completed}轮，等待卖出机会"
            sell_state = (
                "disabled"
                if completed >= self.strategy_config.max_cycles or cooling_down
                else "normal"
            )
            buy_state = "disabled"
        else:
            target = pending.price * (1 - self.strategy_config.buyback_discount)
            chase = pending.price * (1 + self.strategy_config.chase_back_rise)
            status = (
                f"实盘待买回：卖 {pending.price:.2f} / 目标≤{target:.2f} "
                f"/ 保护≥{chase:.2f}"
            )
            sell_state, buy_state = "disabled", "normal"
        self.context_menu.entryconfigure(self.actual_status_menu_index, label=status)
        self.context_menu.entryconfigure(self.actual_sell_menu_index, state=sell_state)
        self.context_menu.entryconfigure(self.actual_buy_menu_index, state=buy_state)

    def _equity_summary(self) -> str:
        event = self.equity_event
        if event is None:
            return "股权事件：暂无即将实施的分红/送转安排"
        record = event.record_date.strftime("%m-%d") if event.record_date else "--"
        ex_date = (
            event.ex_dividend_date.strftime("%m-%d")
            if event.ex_dividend_date
            else "--"
        )
        payout = event.payout_date.strftime("%m-%d") if event.payout_date else "--"
        return (
            f"股权事件：登记 {record}（{event.days_until}天）/ "
            f"除权 {ex_date} / 派息 {payout}"
        )

    def _update_equity_menu(self) -> None:
        self.context_menu.entryconfigure(self.equity_menu_index, label=self._equity_summary())

    @staticmethod
    def _ema(values: list[float], period: int) -> list[float]:
        return ema_values(values, period)

    @classmethod
    def _candidate_sides(
        cls,
        bars: list[MinuteBar],
        points: list[MacdPoint],
        mode: str,
    ) -> list[str | None]:
        if mode == "histogram":
            return histogram_shrink_sides(points)
        return candidate_sides(bars, points, mode)

    @staticmethod
    def _chart_coordinates(
        values: list[float], width: int, height: int, low: float, span: float
    ) -> list[float]:
        coordinates: list[float] = []
        for index, value in enumerate(values):
            x = 3 + (width - 6) * index / max(len(values) - 1, 1)
            y = 3 + (height - 6) * (low + span - value) / span
            coordinates.extend((x, y))
        return coordinates

    @staticmethod
    def _market_color(previous: float, current: float) -> str:
        if current > previous:
            return "#e45b65"
        if current < previous:
            return "#36c98f"
        return "#aeb6c2"

    @staticmethod
    def _change_color(change: float) -> str:
        if change > 0:
            return "#e45b65"
        if change < 0:
            return "#36c98f"
        return "#929aa8"

    def _set_context_labels(
        self, market_text: str, sector_text: str, market_color: str, sector_color: str
    ) -> None:
        self.market_context_label.config(text=market_text, fg=market_color)
        self.sector_context_label.config(text=sector_text, fg=sector_color)

    def _update_board_menu(
        self, boards: list[StockBoard], selected: StockBoard | None
    ) -> None:
        signature = tuple((board.kind, board.code, board.name) for board in boards)
        if signature == self.board_menu_signature:
            if selected is not None:
                self.board_code_var.set(selected.code)
            return
        self.board_menu.delete(0, "end")
        for kind_index, kind in enumerate(("行业", "概念")):
            matching = [board for board in boards if board.kind == kind]
            if not matching:
                continue
            if kind_index and self.board_menu.index("end") is not None:
                self.board_menu.add_separator()
            for board in matching:
                self.board_menu.add_radiobutton(
                    label=f"{kind} · {board.name}",
                    variable=self.board_code_var,
                    value=board.code,
                    command=self._change_board,
                )
        self.board_menu_signature = signature
        if selected is not None:
            self.board_code_var.set(selected.code)

    def _change_board(self) -> None:
        board_code = self.board_code_var.get()
        board = next(
            (item for item in self.available_boards if item.code == board_code), None
        )
        if board is None:
            return
        with self.state_lock:
            self.selected_board = board
            self.context_snapshot = None
            self.context_updated = 0.0
        self._set_context_labels("正在切换", board.name, "#929aa8", "#929aa8")
        self.refresh_event.set()

    def _reverse_t_actions(
        self,
        bars: list[MinuteBar],
        points: list[MacdPoint],
        code: str | None = None,
        mode: str | None = None,
    ) -> list[str | None]:
        if not points:
            return []
        active_code = code or self.code
        active_mode = mode or self.candidate_mode
        raw_candidates = candidate_sides(bars, points, active_mode)
        force_index = next(
            (
                index
                for index, point in enumerate(points)
                if point.time.strftime("%H:%M") >= "14:50"
            ),
            None,
        )
        pending = self.trade_journal.open_sell(active_code, points[-1].time.date())
        if pending is not None:
            return actual_buyback_reminder_sides(
                points,
                raw_candidates,
                sell_price=pending.price,
                sell_time=pending.occurred_at,
                config=self.strategy_config,
                force_buy_back_index=force_index,
            )
        completed = self.trade_journal.completed_cycles(
            active_code, points[-1].time.date()
        )
        latest = self.trade_journal.latest_trade(active_code, points[-1].time.date())
        if completed >= self.strategy_config.max_cycles:
            return [None] * len(points)
        if (
            latest is not None
            and latest.side == "BUY"
            and points[-1].time - latest.occurred_at
            < timedelta(minutes=self.strategy_config.cooldown_minutes)
        ):
            return [None] * len(points)
        guarded = guarded_reverse_t_action_sides(
            bars,
            points,
            raw_candidates,
            config=self.strategy_config,
            force_buy_back_index=force_index,
        )
        # 未手工确认实际卖出前，只提示可卖位置；模拟出的买回点不能冒充真实持仓状态。
        return [side if side == "SELL" else None for side in guarded]

    def _update_recent_action_menu(
        self, points: list[MacdPoint], actions: list[str | None]
    ) -> None:
        latest: dict[str, MacdPoint] = {}
        for point, side in zip(points, actions, strict=True):
            if side is not None:
                latest[side] = point
        sell = latest.get("SELL")
        buy = latest.get("BUY")
        sell_text = (
            f"最近卖出：{sell.time:%H:%M}  {sell.close:.2f}"
            if sell is not None
            else "最近卖出：暂无"
        )
        buy_text = (
            f"最近买回：{buy.time:%H:%M}  {buy.close:.2f}"
            if buy is not None
            else "最近买回：暂无"
        )
        self.context_menu.entryconfigure(
            self.recent_sell_menu_index, label=sell_text
        )
        self.context_menu.entryconfigure(self.recent_buy_menu_index, label=buy_text)

    def _draw_price(
        self,
        bars: list[MinuteBar],
        points: list[MacdPoint],
        candidates: list[str | None] | None = None,
    ) -> None:
        canvas = self.price_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), self.CHART_WIDTH)
        height = max(canvas.winfo_height(), self.HEIGHT)
        values = [point.close for point in points]
        ema20 = self._ema(values, 20)
        vwap = session_vwap_values(bars)
        visible_values = values + ema20 + vwap
        low, high = min(visible_values), max(visible_values)
        span = high - low or 1
        coordinates = self._chart_coordinates(values, width, height, low, span)
        ema_coordinates = self._chart_coordinates(ema20, width, height, low, span)
        vwap_coordinates = self._chart_coordinates(vwap, width, height, low, span)
        if len(vwap_coordinates) >= 4:
            canvas.create_line(*vwap_coordinates, fill="#aeb6c2", width=1)
        if len(ema_coordinates) >= 4:
            canvas.create_line(*ema_coordinates, fill="#e5b94f", width=1)
        for index in range(1, len(values)):
            offset = index * 2
            canvas.create_line(
                coordinates[offset - 2],
                coordinates[offset - 1],
                coordinates[offset],
                coordinates[offset + 1],
                fill=self._market_color(values[index - 1], values[index]),
                width=1.5,
            )
        candidates = candidates or self._reverse_t_actions(bars, points)
        marked_indexes = [
            index for index, side in enumerate(candidates) if side is not None
        ]
        latest_index = marked_indexes[-1] if marked_indexes else None
        for index, side in enumerate(candidates):
            if side == "BUY":
                color = "#e45b65"
            elif side == "SELL":
                color = "#36c98f"
            else:
                continue
            current = points[index]
            x = 3 + (width - 6) * index / max(len(points) - 1, 1)
            y = 3 + (height - 6) * (high - current.close) / span
            outline = "#f3f4f6" if index == latest_index else ""
            if side == "BUY":
                coordinates = (x, y - 5, x - 3.5, y + 2, x + 3.5, y + 2)
            else:
                coordinates = (x, y + 5, x - 3.5, y - 2, x + 3.5, y - 2)
            canvas.create_polygon(
                *coordinates,
                fill=color,
                outline=outline,
                width=1,
            )

    def _draw_volume(
        self, bars: list[MinuteBar], points: list[MacdPoint]
    ) -> None:
        canvas = self.volume_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), self.CHART_WIDTH)
        height = max(canvas.winfo_height(), self.HEIGHT)
        recent_bars = bars[-48:]
        recent_points = points[-len(recent_bars) :]
        volumes = [max(bar.volume, 0) for bar in recent_bars]
        maximum = max(max(volumes, default=0), 1)
        latest_volume = volumes[-1] if volumes else 0
        canvas.create_text(
            3,
            2,
            anchor="nw",
            text=f"V {latest_volume / 10000:.1f}w" if volumes else "V --",
            fill="#aeb6c2",
            font=("Microsoft YaHei UI", 7, "bold"),
        )
        if not volumes or not recent_points:
            return
        step = (width - 6) / len(volumes)
        for index, volume in enumerate(volumes):
            x = 3 + step * (index + 0.5)
            bar_height = (height - 12) * volume / maximum
            previous_close = (
                recent_points[index - 1].close
                if index > 0 and index - 1 < len(recent_points)
                else recent_points[index].close
            )
            close = recent_points[index].close
            canvas.create_rectangle(
                x - max(step * 0.36, 1),
                height - bar_height,
                x + max(step * 0.36, 1),
                height,
                fill="#b9787d" if close >= previous_close else "#639887",
                outline="",
            )

    def _draw_macd(self, points: list[MacdPoint]) -> None:
        canvas = self.macd_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), self.CHART_WIDTH)
        height = max(canvas.winfo_height(), self.HEIGHT)
        recent = points[-48:]
        values = [point.histogram for point in recent]
        indicator_values = (
            values + [point.dif for point in recent] + [point.dea for point in recent]
        )
        maximum = max(max(abs(value) for value in indicator_values), 0.000001)
        zero_y = height / 2
        canvas.create_line(3, zero_y, width - 3, zero_y, fill="#40454f")
        step = (width - 6) / max(len(values), 1)
        for index, value in enumerate(values):
            x = 3 + step * (index + 0.5)
            y = zero_y - value / maximum * (height / 2 - 3)
            canvas.create_line(
                x,
                zero_y,
                x,
                y,
                fill="#e45b65" if value >= 0 else "#36c98f",
                width=min(4, max(1, int(step * 0.65))),
            )
        bounds = (-maximum, maximum)
        dif = self._scaled_macd_coordinates(
            [point.dif for point in recent], width, height, bounds
        )
        dea = self._scaled_macd_coordinates(
            [point.dea for point in recent], width, height, bounds
        )
        if len(dif) >= 4:
            canvas.create_line(*dif, fill="#e5b94f", width=1)
            canvas.create_line(*dea, fill="#55b6d7", width=1)

    @staticmethod
    def _scaled_macd_coordinates(
        values: list[float],
        width: int,
        height: int,
        bounds: tuple[float, float],
    ) -> list[float]:
        low, high = bounds
        span = high - low or 1
        coordinates: list[float] = []
        for index, value in enumerate(values):
            x = 3 + (width - 6) * index / max(len(values) - 1, 1)
            y = 3 + (height - 6) * (high - value) / span
            coordinates.extend((x, y))
        return coordinates

    def _show_signal(self, signal: MacdSignal, guarded: bool = True) -> None:
        if not self.desktop_settings["notifications"]:
            return
        buy = signal.side == "BUY"
        color = "#e45b65" if buy else "#36c98f"
        self.alert_generation += 1
        generation = self.alert_generation
        self.signal_strip.config(bg=color)
        marker = "回" if buy and guarded else ("卖" if not buy and guarded else "观")
        self.price_label.config(text=f"{marker}{signal.price:.2f}", fg=color)
        context = self.context_snapshot
        strength = context.reverse_sell_strength if context is not None else "--"
        left_text = (
            ("买回1成" if guarded else "观察")
            if buy
            else ("卖1成" if guarded else "观察")
        )
        pending = self.trade_journal.open_sell(self.code) if buy else None
        reason = self._signal_reason(
            signal.side,
            self.candidate_mode,
            price=signal.price,
            sell_price=pending.price if pending is not None else None,
            guarded=guarded,
            config=self.strategy_config,
        )
        change = getattr(self, "latest_change", None)
        change_text = f"涨跌 {change:+.2%}" if change is not None else "涨跌 --"
        price_volume_context = self._price_volume_context()
        right_text = (
            f"{reason}·{change_text}·{strength}·{price_volume_context} "
            f"{signal.time:%H:%M}"
        )
        self._set_context_labels(left_text, right_text, color, color)
        # 仅在任务栏小条内加强提醒，不创建独立弹窗。
        if self.desktop_settings["flash"]:
            self._pulse_signal(generation, color, flashes=14)
            self._flash_taskbar()
        audio_allowed = self._allow_audio(signal.side, guarded)
        if audio_allowed and self.desktop_settings["sound"]:
            threading.Thread(target=self._play_alert_sound, args=(signal.side,), daemon=True).start()
        if audio_allowed and self.desktop_settings["speech"]:
            threading.Thread(
                target=self._speak_alert,
                args=(signal.side, signal.price, change, guarded, price_volume_context),
                daemon=True,
            ).start()
        self.root.after(self.desktop_settings["alertSeconds"] * 1000, lambda: self._clear_signal(generation))

    def _flash_taskbar(self) -> None:
        """让 Windows 任务栏按钮短暂闪烁，不创建额外窗口。"""
        if sys.platform != "win32":
            return
        try:
            info = FlashWindowInfo(
                ctypes.sizeof(FlashWindowInfo), self.root.winfo_id(), 0x0000000F, 6, 0
            )
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except (AttributeError, OSError):
            pass

    @staticmethod
    def _signal_reason(
        side: str,
        mode: str,
        price: float | None = None,
        sell_price: float | None = None,
        guarded: bool = True,
        config: IntradayStrategyConfig | None = None,
    ) -> str:
        config = config or IntradayStrategyConfig()
        if mode == "histogram":
            if side == "SELL" and not guarded:
                return "红柱连续3次变短·未达VWAP高位"
            if side == "BUY" and not guarded:
                return "绿柱连续3次变短·未记录卖出"
            if side == "BUY" and price is not None and sell_price is not None:
                if price >= sell_price * (1 + config.chase_back_rise):
                    return "卖飞保护买回"
                if price <= sell_price * (1 - config.buyback_discount):
                    return "价差达标+绿柱缩短"
            return "绿柱连续3次变短" if side == "BUY" else "红柱连续3次变短"
        return "金叉" if side == "BUY" else "死叉"

    def _pulse_signal(self, generation: int, color: str, flashes: int) -> None:
        if generation != self.alert_generation:
            return
        if flashes <= 0:
            self.signal_strip.config(bg=color, width=5)
            return
        flash_color = "#ffffff" if flashes % 2 == 0 else color
        self.signal_strip.config(bg=flash_color, width=8)
        self.price_label.config(fg=flash_color)
        self.root.after(180, lambda: self._pulse_signal(generation, color, flashes - 1))

    @staticmethod
    def _play_alert_sound(side: str) -> None:
        frequencies = (880, 1100, 1320, 1560) if side == "BUY" else (980, 760, 580, 420)
        try:
            for frequency in frequencies:
                winsound.Beep(frequency, 220)
                time.sleep(0.08)
        except RuntimeError:
            ctypes.windll.user32.MessageBeep(
                0x00000040 if side == "BUY" else 0x00000030
            )

    @staticmethod
    def _speak_alert(
        side: str,
        price: float,
        change: float | None,
        guarded: bool,
        price_volume_context: str = "",
    ) -> None:
        """组装买卖信号语音文本并后台播报。"""
        action = (
            "买回提醒"
            if side == "BUY" and guarded
            else "买回观察"
            if side == "BUY"
            else "卖出提醒"
            if guarded
            else "卖出观察"
        )
        change_text = "涨跌幅未知" if change is None else f"涨跌 {change:+.2%}"
        context_text = (
            f"，{price_volume_context}" if price_volume_context else ""
        )
        speech = f"{action}，价格 {price:.2f}，{change_text}{context_text}"
        _speak_text(speech)

    def _test_signal(self, side: str) -> None:
        point = self.latest_point
        signal = MacdSignal(
            time=point.time if point else datetime.now(CHINA_TZ),
            side=side,
            price=point.close if point else 0.0,
            dif=point.dif if point else 0.0,
            dea=point.dea if point else 0.0,
            histogram=point.histogram if point else 0.0,
        )
        self._show_signal(signal)

    def _start_backtest(self) -> None:
        if self.backtest_running:
            return
        with self.state_lock:
            code = self.code
            mode = self.candidate_mode
        self.backtest_running = True
        self.context_menu.entryconfigure(
            self.backtest_menu_index,
            label="正在获取历史数据...",
            state="disabled",
        )
        threading.Thread(
            target=self._backtest_worker, args=(code, mode), daemon=True
        ).start()

    def _backtest_worker(self, code: str, mode: str) -> None:
        try:
            bars = completed_bars(fetch_sina_history_bars(code, limit=self.desktop_settings["historyBars"], timeout=self.desktop_settings["contextTimeout"]))
            result = run_backtest(bars, mode, config=self.strategy_config)
            self.messages.put(("backtest", code, mode, result))
        except (OSError, RuntimeError, ValueError) as exc:
            self.messages.put(("backtest_error", code, str(exc)))

    def _show_backtest(self, code: str, mode: str, result: BacktestResult) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"{code} 历史回测")
        window.configure(bg="#17191d")
        window.attributes("-topmost", True)
        if sys.platform == "win32":
            window.attributes("-toolwindow", True)
        window.geometry("720x400")
        window.minsize(620, 340)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(2, weight=1)

        mode_labels = {"sensitive": "灵敏", "balanced": "平衡", "steady": "稳健"}
        buy_count = sum(event.signal.side == "BUY" for event in result.events)
        sell_count = sum(event.signal.side == "SELL" for event in result.events)
        reverse_t = sum(trade.direction == "REVERSE_T" for trade in result.trades)
        forced_count = sum(trade.forced_exit for trade in result.trades)
        start = result.bars[0].time
        end = result.bars[-1].time
        title = tk.Label(
            window,
            text=f"{code}  1分钟先卖后买回测  {mode_labels[mode]}公式 / 每次1成",
            bg="#17191d",
            fg="#f3f4f6",
            anchor="w",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 3))
        summary = tk.Label(
            window,
            text=(
                f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}   {len(result.bars)} 根   "
                f"买 {buy_count} / 卖 {sell_count}   "
                f"15分钟命中 {result.hit_rate:.1%}   "
                f"反T {reverse_t} / 收盘强制买回 {forced_count}   "
                f"做T胜率 {result.trade_win_rate:.1%}   "
                f"价差累计 {result.cumulative_spread_return:+.1%}   "
                f"总仓贡献 {result.cumulative_return:+.2%}   "
                f"总仓回撤 {result.max_drawdown:.2%}"
            ),
            bg="#17191d",
            fg="#b8bec8",
            anchor="w",
            justify="left",
            wraplength=680,
            font=("Microsoft YaHei UI", 8),
        )
        summary.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        style = ttk.Style(window)
        style.configure("Backtest.TNotebook", background="#17191d", borderwidth=0)
        style.configure(
            "Backtest.TNotebook.Tab",
            background="#252a31",
            foreground="#17191d",
            padding=(12, 5),
        )
        style.map(
            "Backtest.TNotebook.Tab",
            background=[("selected", "#3a424d")],
            foreground=[("selected", "#17191d")],
        )
        notebook = ttk.Notebook(window, style="Backtest.TNotebook")
        notebook.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        for trade_date in sorted({bar.time.date() for bar in result.bars}):
            session_bars = [bar for bar in result.bars if bar.time.date() == trade_date]
            session_events = [
                event
                for event in result.events
                if event.signal.time.date() == trade_date
            ]
            session_trades = [
                trade
                for trade in result.trades
                if trade.entry_time.date() == trade_date
            ]
            session_spread_return = 1.0
            session_account_return = 1.0
            for trade in session_trades:
                session_spread_return *= 1 + trade.return_rate
                session_account_return *= 1 + trade.account_return
            frame = tk.Frame(notebook, bg="#111317")
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(1, weight=1)
            detail = tk.Label(
                frame,
                text=(
                    f"候选 {len(session_events)}   反T {len(session_trades)}   "
                    f"价差 {session_spread_return - 1:+.2%}   "
                    f"总仓贡献 {session_account_return - 1:+.2%}"
                ),
                bg="#111317",
                fg="#aeb6c2",
                anchor="w",
                font=("Microsoft YaHei UI", 8),
            )
            detail.grid(row=0, column=0, sticky="ew", padx=6, pady=(5, 0))
            canvas = tk.Canvas(frame, bg="#111317", highlightthickness=0)
            canvas.grid(row=1, column=0, sticky="nsew")
            canvas.bind(
                "<Configure>",
                lambda _event, target=canvas, bars=session_bars, events=session_events, trades=session_trades: (
                    self._draw_backtest_chart(target, bars, events, trades)
                ),
            )
            notebook.add(frame, text=f"{trade_date:%m/%d}")
        window.update_idletasks()
        x = (window.winfo_screenwidth() - window.winfo_width()) // 2
        y = (window.winfo_screenheight() - window.winfo_height()) // 2
        window.geometry(f"+{x}+{y}")

    @staticmethod
    def _draw_backtest_chart(
        canvas: tk.Canvas,
        bars: list[MinuteBar],
        events: list[BacktestEvent],
        trades: list[BacktestTrade],
    ) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        padding = 18
        values = [bar.close for bar in bars]
        low, high = min(values), max(values)
        span = high - low or 1
        usable_width = max(width - padding * 2, 1)
        usable_height = max(height - padding * 2, 1)
        point_positions: dict[datetime, tuple[float, float]] = {}
        previous_position: tuple[float, float] | None = None
        for index, bar in enumerate(bars):
            x = padding + usable_width * index / max(len(bars) - 1, 1)
            y = padding + usable_height * (high - bar.close) / span
            point_positions[bar.time] = (x, y)
            if previous_position is not None:
                previous_bar = bars[index - 1]
                canvas.create_line(
                    *previous_position,
                    x,
                    y,
                    fill=MacdTaskbarBar._market_color(previous_bar.close, bar.close),
                    width=1.5,
                )
            previous_position = (x, y)
        for trade in trades:
            entry = point_positions.get(trade.entry_time)
            exit_position = point_positions.get(trade.exit_time)
            if entry is None or exit_position is None:
                continue
            canvas.create_line(
                *entry,
                *exit_position,
                fill="#e5b94f",
                width=1,
                dash=(3, 2),
            )
            canvas.create_text(
                (entry[0] + exit_position[0]) / 2,
                (entry[1] + exit_position[1]) / 2 - 7,
                text=("正T" if trade.direction == "POSITIVE_T" else "反T")
                + ("收" if trade.forced_exit else ""),
                fill="#e5b94f",
                font=("Microsoft YaHei UI", 7, "bold"),
            )
        for event in events:
            position = point_positions.get(event.signal.time)
            if position is None:
                continue
            x, y = position
            if event.signal.side == "BUY":
                canvas.create_polygon(
                    x,
                    y - 5,
                    x - 3,
                    y + 1,
                    x + 3,
                    y + 1,
                    fill="#e45b65",
                    outline="",
                )
                badge_y = min(y + 13, height - 10)
                badge_color = "#e45b65"
                badge_text = "买"
            else:
                canvas.create_polygon(
                    x,
                    y + 5,
                    x - 3,
                    y - 1,
                    x + 3,
                    y - 1,
                    fill="#36c98f",
                    outline="",
                )
                badge_y = max(y - 13, 10)
                badge_color = "#36c98f"
                badge_text = "卖"
            canvas.create_oval(
                x - 7,
                badge_y - 7,
                x + 7,
                badge_y + 7,
                fill=badge_color,
                outline="#f3f4f6",
                width=1,
            )
            canvas.create_text(
                x,
                badge_y,
                text=badge_text,
                fill="#ffffff",
                font=("Microsoft YaHei UI", 7, "bold"),
            )
        canvas.create_text(
            width - 8,
            6,
            anchor="ne",
            text="红：上涨 / 买    绿：下跌 / 卖",
            fill="#d8dde5",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        canvas.create_text(
            6,
            5,
            anchor="nw",
            text=f"{high:.2f}",
            fill="#8f98a6",
            font=("Microsoft YaHei UI", 7),
        )
        canvas.create_text(
            6,
            height - 5,
            anchor="sw",
            text=f"{low:.2f}",
            fill="#8f98a6",
            font=("Microsoft YaHei UI", 7),
        )

    def _change_candidate_mode(self) -> None:
        mode = self.candidate_mode_var.get()
        if mode not in CANDIDATE_RULES:
            return
        with self.state_lock:
            self.candidate_mode = mode
            self.last_processed = None
        self.refresh_event.set()

    def _show_insight_window(self) -> None:
        """Show a compact, always-on-top explanation window above the taskbar bar."""
        existing = getattr(self, "insight_window", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            return

        window = tk.Toplevel(self.root)
        self.insight_window = window
        window.title("AStock 情绪与指标")
        window.configure(bg="#20272d")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        if sys.platform == "win32":
            window.attributes("-toolwindow", True)
        width, height = 430, 325
        x = max(self.root.winfo_x(), 0)
        y = max(self.root.winfo_y() - height - 6, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")

        title_row = tk.Frame(window, bg="#20272d")
        title_row.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(
            title_row,
            text=f"{self.code}  情绪与指标",
            bg="#20272d",
            fg="#f3f4f6",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")
        tk.Button(
            title_row,
            text="×",
            command=window.destroy,
            bg="#20272d",
            fg="#929aa8",
            activebackground="#e45b65",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            width=2,
        ).pack(side="right")

        sentiment = self._insight_sentiment_text()
        self.insight_sentiment_label = tk.Label(
            window,
            text=sentiment,
            justify="left",
            anchor="w",
            wraplength=405,
            bg="#20272d",
            fg="#f0c674",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.insight_sentiment_label.pack(fill="x", padx=10, pady=(2, 5))

        self.insight_price_volume_label = tk.Label(
            window,
            text=self._price_volume_insight_text(),
            justify="left",
            anchor="w",
            wraplength=405,
            bg="#20272d",
            fg="#d8dde5",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.insight_price_volume_label.pack(fill="x", padx=10, pady=(0, 6))

        self.insight_equity_label = tk.Label(
            window,
            text=self._equity_summary(),
            justify="left",
            anchor="w",
            wraplength=405,
            bg="#20272d",
            fg="#e5b94f",
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.insight_equity_label.pack(fill="x", padx=10, pady=(0, 5))

        indicator_text = (
            "当前柱缩规则：绿柱连续3次变短 = 空头动能衰减，提示买回观察；"
            "红柱连续3次变短 = 多头动能衰减，提示卖出观察。3次变短需要连续4根同色柱。\n"
            "反T条件：先出现卖出候选并减仓1成，之后才允许买回1成；没有先卖，不执行买回。"
        )
        tk.Label(
            window,
            text=indicator_text,
            justify="left",
            anchor="w",
            wraplength=405,
            bg="#20272d",
            fg="#d8dde5",
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=10, pady=2)

        tk.Label(
            window,
            text="说明：情绪为大盘/板块动量摘要，不等同于新闻；信号是候选观察，不是自动交易指令。",
            justify="left",
            anchor="w",
            wraplength=405,
            bg="#20272d",
            fg="#929aa8",
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=10, pady=(4, 8))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        self._refresh_insight_window()

    def _refresh_insight_window(self) -> None:
        window = getattr(self, "insight_window", None)
        label = getattr(self, "insight_sentiment_label", None)
        if window is None or label is None or not window.winfo_exists():
            return
        label.config(text=self._insight_sentiment_text())
        insight_label = getattr(self, "insight_price_volume_label", None)
        if insight_label is not None:
            insight_label.config(text=self._price_volume_insight_text())
        equity_label = getattr(self, "insight_equity_label", None)
        if equity_label is not None:
            equity_label.config(text=self._equity_summary())
        window.after(3000, self._refresh_insight_window)

    def _insight_sentiment_text(self) -> str:
        context = self.context_snapshot
        point = self.latest_point
        if context is None:
            return "最新情绪：正在获取大盘与板块数据"
        if context.market_momentum > 0 and context.sector_momentum > 0:
            mood = "偏强"
        elif context.market_momentum < 0 and context.sector_momentum < 0:
            mood = "偏弱"
        else:
            mood = "分化"
        macd = "MACD 多头" if point is not None and point.histogram > 0 else "MACD 空头"
        return (
            f"最新情绪：{mood} · 盘 {context.market_change:+.2%} · "
            f"板 {context.sector_change:+.2%} · {macd}\n"
            f"板块强弱 {context.sector_relative_strength:+.2%} · "
            f"量比 {context.sector_volume_ratio:.2f} · "
            f"动量评分 {context.sector_sentiment_score}/100"
        )

    def _price_volume_insight_text(self) -> str:
        signal = self._price_volume_signal()
        if signal is None:
            return "价量提示：等待足够的分钟行情"
        return "价量提示：" + signal[1]

    def _compute_price_volume_state(self) -> PriceVolumeState | None:
        bars = self.latest_bars
        points = self.latest_points
        if len(bars) < 2 or len(points) < 2:
            return None
        latest_volume = max(bars[-1].volume, 0)
        baseline = [max(bar.volume, 0) for bar in bars[-6:-1]]
        average_volume = sum(baseline) / len(baseline) if baseline else 0
        ratio = latest_volume / average_volume if average_volume else 0
        return PriceVolumeState(
            ratio=ratio,
            volume_state=(
                "放量"
                if ratio >= self.desktop_settings["volumeSurge"]
                else "缩量"
                if ratio <= self.desktop_settings["volumeShrink"]
                else "平量"
            ),
            price_up=bars[-1].close >= bars[-2].close,
            histogram_growing=abs(points[-1].histogram)
            > abs(points[-2].histogram),
        )

    def _price_volume_context(self) -> str:
        state = self._compute_price_volume_state()
        if state is None:
            return "价量数据不足"
        return f"{state.direction}{state.volume_state}，{state.momentum}"

    def _price_volume_signal(self) -> tuple[str, str] | None:
        bars = self.latest_bars
        state = self._compute_price_volume_state()
        if state is None:
            return None
        if (
            state.price_up
            and state.ratio >= self.desktop_settings["volumeSurge"]
            and state.histogram_growing
        ):
            return "confirm", "上涨放量，MACD 柱变长，走势确认度较好"
        if (
            state.price_up
            and state.ratio <= self.desktop_settings["volumeShrink"]
            and not state.histogram_growing
        ):
            return "weak", "上涨缩量，MACD 柱变短，上涨可能乏力"
        if not state.price_up and state.ratio >= self.desktop_settings["volumeSurge"]:
            return "sell_pressure", "下跌放量，抛压需要警惕"
        prior_closes = [bar.close for bar in bars[-11:-1]]
        if (
            prior_closes
            and bars[-1].close >= max(prior_closes)
            and state.ratio < self.desktop_settings["volumeSurge"]
        ):
            return "divergence", "价格创新高，但成交量没有同步放大，注意量价背离"
        return "normal", f"当前{state.direction}，{state.volume_state}，{state.momentum}"

    def _show_context_menu(self, event: tk.Event) -> None:
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _clear_signal(self, generation: int) -> None:
        if generation != self.alert_generation:
            return
        self.signal_strip.config(bg="#555b66", width=3)
        self.price_label.config(fg=self.foreground_color)

    def _show_fully(self, _event: tk.Event) -> None:
        self.root.lift()

    def _start_drag(self, event: tk.Event) -> None:
        if not self.desktop_settings["dragEnabled"] or event.widget in (self.code_entry, self.close_button):
            return
        self.drag_origin = (
            event.x_root - self.root.winfo_rootx(),
            event.y_root - self.root.winfo_rooty(),
        )
        self._drag_moved = False

    def _drag(self, event: tk.Event) -> None:
        if self.drag_origin is None:
            return
        offset_x, offset_y = self.drag_origin
        x, y = event.x_root - offset_x, event.y_root - offset_y
        if not self._drag_moved and abs(x - self.root.winfo_rootx()) + abs(y - self.root.winfo_rooty()) < 4:
            return
        self._drag_moved = True
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        hwnd = int(user32.GetParent(self.root.winfo_id()) or self.root.winfo_id())
        user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015)  # move, no resize/z-order/activation

    def _save_position(self, changes: dict) -> None:
        if self.config_path:
            # Merge current disk settings so a drag cannot undo a uTools edit.
            settings = {**read_settings(self.config_path), **changes}
            temporary = self.config_path.with_suffix('.position.tmp')
            temporary.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.config_path)
            self._reload_settings()
        else:
            self.desktop_settings.update(changes)
            self._dock_inside_taskbar()

    def _end_drag(self, _event: tk.Event) -> None:
        if self.drag_origin is None:
            return
        self.drag_origin = None
        if self._drag_moved:
            self.root.update_idletasks()
            self._save_position({"dockMode": "floating", "windowX": self.root.winfo_rootx(), "windowY": self.root.winfo_rooty()})

    def _restore_docking(self, _event: tk.Event) -> None:
        if not self.desktop_settings["dragEnabled"] or _event.widget in (self.code_entry, self.close_button):
            return
        self.drag_origin = None
        self._save_position({"dockMode": "taskbar"})


def run_taskbar_bar(code: str | None, poll_seconds: int, config_path: Path | None = None) -> int:
    global _TASKBAR_MUTEX
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.GetLastError.restype = ctypes.c_ulong
        _TASKBAR_MUTEX = int(
            kernel32.CreateMutexW(None, True, "Global\\AStockMacdTaskbarBar") or 0
        )
        if kernel32.GetLastError() == 183:
            if config_path is not None:
                print("行情条已在运行，等待原窗口读取新的 uTools 配置。", flush=True)
                return 3
            show_popup("AStock 行情条已在运行", "请在现有任务栏行情条中更换股票或板块。")
            return 0
    try:
        return MacdTaskbarBar(code, poll_seconds, config_path).run()
    except ValueError as exc:
        show_popup("AStock MACD 启动失败", str(exc), warning=True)
        return 2


def main() -> int:
    args = parse_args()
    if args.poll < 3:
        print("参数错误：--poll 不能小于 3 秒", file=sys.stderr)
        return 2
    if args.tray or args.bar:
        return run_taskbar_bar(args.code, args.poll, args.settings)
    if args.gui:
        return run_gui(args.code, args.poll, show_notifications=not args.no_popup, config_path=args.settings)
    raw_code = args.code or input("请输入要盯盘的 6 位股票代码（如 600519）：")
    try:
        code = normalize_stock_code(raw_code)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    print(f"开始监控 {code}，每 {args.poll} 秒检查一次；按 Ctrl+C 停止。")
    last_processed = None
    try:
        while True:
            try:
                name, latest, signal = check_once(code)
                is_new_bar = latest.time != last_processed
                if is_new_bar:
                    state = "多头" if latest.histogram > 0 else "空头"
                    print(
                        f"[{datetime.now(CHINA_TZ):%H:%M:%S}] {name} {latest.time:%H:%M} "
                        f"收盘 {latest.close:.2f} MACD柱 {latest.histogram:.4f}（{state}）",
                        flush=True,
                    )
                    if (
                        signal is not None
                        and signal.time.date() == datetime.now(CHINA_TZ).date()
                    ):
                        notify(name, code, signal, popup=not args.no_popup)
                    last_processed = latest.time
                if args.once:
                    return 0
            except (OSError, RuntimeError, ValueError) as exc:
                print(
                    f"[{datetime.now(CHINA_TZ):%H:%M:%S}] 行情检查失败：{exc}",
                    file=sys.stderr,
                    flush=True,
                )
                if args.once:
                    return 1
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\n已停止监控。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
