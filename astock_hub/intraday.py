from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_REALTIME_TRENDS_URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_F10_URL = "https://datacenter.eastmoney.com/securities/api/data/get"
EASTMONEY_DIVIDEND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TENCENT_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SINA_MINUTE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_astock_history=/"
    "CN_MarketDataService.getKLineData"
)
CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class MinuteBar:
    time: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    average: float


@dataclass(frozen=True)
class MacdPoint:
    time: datetime
    close: float
    dif: float
    dea: float
    histogram: float


@dataclass(frozen=True)
class MacdSignal:
    time: datetime
    side: str
    price: float
    dif: float
    dea: float
    histogram: float


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    volume_lots: int

    @property
    def volume_shares(self) -> int:
        return self.volume_lots * 100


@dataclass(frozen=True)
class OrderBook:
    code: str
    name: str
    last_price: float
    upper_limit: float
    lower_limit: float
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    quote_time: datetime | None

    @property
    def sealed_side(self) -> str | None:
        if (
            self.upper_limit > 0
            and self.bids
            and self.bids[0].volume_lots > 0
            and abs(self.last_price - self.upper_limit) < 0.005
            and abs(self.bids[0].price - self.upper_limit) < 0.005
        ):
            return "UP"
        if (
            self.lower_limit > 0
            and self.asks
            and self.asks[0].volume_lots > 0
            and abs(self.last_price - self.lower_limit) < 0.005
            and abs(self.asks[0].price - self.lower_limit) < 0.005
        ):
            return "DOWN"
        return None

    @property
    def sealed_volume_lots(self) -> int:
        if self.sealed_side == "UP":
            return self.bids[0].volume_lots
        if self.sealed_side == "DOWN":
            return self.asks[0].volume_lots
        return 0


@dataclass(frozen=True)
class IntradayContext:
    market_name: str
    market_change: float
    market_momentum: float
    sector_name: str
    sector_change: float
    sector_momentum: float

    @property
    def reverse_sell_strength(self) -> str:
        weak_count = sum(
            value <= 0 for value in (self.market_momentum, self.sector_momentum)
        )
        return ("弱", "中", "强")[weak_count]


@dataclass(frozen=True)
class StockBoard:
    name: str
    code: str
    kind: str


@dataclass(frozen=True)
class EquityEvent:
    code: str
    name: str
    record_date: date | None
    ex_dividend_date: date | None
    payout_date: date | None
    plan: str
    bonus_rmb: float | None
    days_until: int
    impact: str


def normalize_stock_code(value: str) -> str:
    code = value.strip().upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if code.endswith(suffix):
            code = code[: -len(suffix)]
            break
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix) :]
            break
    if len(code) != 6 or not code.isdigit():
        raise ValueError("股票代码必须是 6 位数字，例如 600519 或 000001")
    return code


def eastmoney_secid(code: str) -> str:
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market}.{code}"


def tencent_symbol(code: str) -> str:
    code = normalize_stock_code(code)
    if code.startswith(("4", "8", "92")):
        exchange = "bj"
    elif code.startswith(("5", "6", "9")):
        exchange = "sh"
    else:
        exchange = "sz"
    return f"{exchange}{code}"


def parse_tencent_order_book(raw: str, code: str) -> OrderBook:
    normalized = normalize_stock_code(code)
    match = re.search(r'="(.*)";?\s*$', raw.strip())
    if match is None:
        raise RuntimeError(f"腾讯未返回 {normalized} 的五档盘口")
    fields = match.group(1).split("~")
    if len(fields) < 49:
        raise RuntimeError(f"腾讯返回的 {normalized} 五档盘口字段不完整")

    def number(index: int) -> float:
        try:
            return float(fields[index] or 0)
        except (IndexError, ValueError) as exc:
            raise RuntimeError(
                f"腾讯返回的 {normalized} 五档盘口数值无效"
            ) from exc

    def levels(start: int) -> tuple[OrderBookLevel, ...]:
        return tuple(
            OrderBookLevel(
                price=number(start + offset * 2),
                volume_lots=int(number(start + offset * 2 + 1)),
            )
            for offset in range(5)
        )

    quote_time = None
    timestamp = fields[30].strip()
    if timestamp:
        try:
            quote_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(
                tzinfo=CHINA_TZ
            )
        except ValueError:
            pass
    return OrderBook(
        code=normalized,
        name=fields[1] or normalized,
        last_price=number(3),
        upper_limit=number(47),
        lower_limit=number(48),
        bids=levels(9),
        asks=levels(19),
        quote_time=quote_time,
    )


def fetch_order_book(code: str, timeout: float = 5) -> OrderBook:
    normalized = normalize_stock_code(code)
    request = Request(
        f"{TENCENT_QUOTE_URL}{tencent_symbol(normalized)}",
        headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("gbk", errors="replace")
    return parse_tencent_order_book(raw, normalized)


def _parse_event_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def parse_equity_event_payload(
    payload: dict[str, object], code: str, today: date | None = None
) -> EquityEvent | None:
    """Select the nearest future dividend/registration event from Eastmoney data."""
    normalized = normalize_stock_code(code)
    current_date = today or datetime.now(CHINA_TZ).date()
    result = payload.get("result")
    rows = result.get("data", []) if isinstance(result, dict) else []
    if not isinstance(rows, list):
        return None
    candidates: list[tuple[date, dict[str, object]]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row_code = str(raw.get("SECURITY_CODE") or "").zfill(6)
        if row_code != normalized:
            continue
        record_date = _parse_event_date(raw.get("EQUITY_RECORD_DATE"))
        ex_date = _parse_event_date(raw.get("EX_DIVIDEND_DATE"))
        payout_date = _parse_event_date(raw.get("PAYOUT_DATE")) or _parse_event_date(
            raw.get("DIVIDEND_DATE")
        )
        upcoming = [item for item in (record_date, ex_date, payout_date) if item]
        future_dates = [item for item in upcoming if item >= current_date]
        if not future_dates:
            continue
        candidates.append((min(future_dates), raw))
    if not candidates:
        return None
    _, raw = min(candidates, key=lambda item: item[0])
    record_date = _parse_event_date(raw.get("EQUITY_RECORD_DATE"))
    ex_date = _parse_event_date(raw.get("EX_DIVIDEND_DATE"))
    payout_date = _parse_event_date(raw.get("PAYOUT_DATE")) or _parse_event_date(
        raw.get("DIVIDEND_DATE")
    )
    upcoming_dates = [item for item in (record_date, ex_date, payout_date) if item]
    next_date = min(item for item in upcoming_dates if item >= current_date)
    bonus_value = raw.get("PRETAX_BONUS_RMB")
    try:
        bonus_rmb = float(bonus_value) if bonus_value is not None else None
    except (TypeError, ValueError):
        bonus_rmb = None
    plan = str(raw.get("IMPL_PLAN_PROFILE") or "分红/送转方案待确认")
    impact = (
        "登记日前持有才享有本次分红/送转；除权除息后价格会按方案机械调整，"
        "实际涨跌仍取决于市场，不代表必然上涨。"
    )
    return EquityEvent(
        code=normalized,
        name=str(raw.get("SECURITY_NAME_ABBR") or normalized),
        record_date=record_date,
        ex_dividend_date=ex_date,
        payout_date=payout_date,
        plan=plan,
        bonus_rmb=bonus_rmb,
        days_until=(next_date - current_date).days,
        impact=impact,
    )


def fetch_equity_event(
    code: str, timeout: float = 8, today: date | None = None
) -> EquityEvent | None:
    normalized = normalize_stock_code(code)
    params = urlencode(
        {
            "reportName": "RPT_SHAREBONUS_DET",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{normalized}")',
            "pageNumber": "1",
            "pageSize": "50",
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
            "source": "HSF10",
            "client": "PC",
        }
    )
    request = Request(
        f"{EASTMONEY_DIVIDEND_URL}?{params}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return parse_equity_event_payload(payload, normalized, today=today)


def market_index_code(code: str) -> str:
    normalize_stock_code(code)
    return "000001"


def fetch_tencent_index_change(index_code: str, timeout: float = 5) -> float:
    if index_code == "000001":
        symbol = "sh000001"
    elif index_code == "399001":
        symbol = "sz399001"
    elif index_code == "899050":
        symbol = "bj899050"
    else:
        raise ValueError(f"不支持的大盘指数代码：{index_code}")
    request = Request(
        f"{TENCENT_QUOTE_URL}{symbol}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("gbk", errors="ignore")
    match = re.search(r'="([^"]+)"', raw)
    if match is None:
        raise RuntimeError(f"腾讯未返回指数 {index_code} 的实时报价")
    fields = match.group(1).split("~")
    if len(fields) <= 4:
        raise RuntimeError(f"腾讯指数 {index_code} 报价字段不完整")
    current = float(fields[3])
    previous_close = float(fields[4])
    if previous_close <= 0:
        raise RuntimeError(f"腾讯指数 {index_code} 昨收价格无效")
    return current / previous_close - 1


def fetch_tencent_stock_change(code: str, timeout: float = 5) -> float:
    code = normalize_stock_code(code)
    if code.startswith(("4", "8", "92")):
        exchange = "bj"
    elif code.startswith(("5", "6", "9")):
        exchange = "sh"
    else:
        exchange = "sz"
    request = Request(
        f"{TENCENT_QUOTE_URL}{exchange}{code}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("gbk", errors="ignore")
    match = re.search(r'="([^"]+)"', raw)
    if match is None:
        raise RuntimeError(f"腾讯未返回股票 {code} 的实时报价")
    fields = match.group(1).split("~")
    if len(fields) <= 4:
        raise RuntimeError(f"腾讯股票 {code} 报价字段不完整")
    current = float(fields[3])
    previous_close = float(fields[4])
    if previous_close <= 0:
        raise RuntimeError(f"腾讯股票 {code} 昨收价格无效")
    return current / previous_close - 1


def _parse_eastmoney_trends(data: dict[str, object]) -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    raw_trends = data.get("trends")
    if not isinstance(raw_trends, list):
        return bars
    for line in raw_trends:
        if not isinstance(line, str):
            continue
        fields = line.split(",")
        if len(fields) < 8:
            continue
        bars.append(
            MinuteBar(
                time=datetime.strptime(fields[0], "%Y-%m-%d %H:%M").replace(
                    tzinfo=CHINA_TZ
                ),
                open=float(fields[1]),
                close=float(fields[2]),
                high=float(fields[3]),
                low=float(fields[4]),
                volume=float(fields[5]),
                amount=float(fields[6]),
                average=float(fields[7]),
            )
        )
    return bars


def _fetch_eastmoney_secid_bars(
    secid: str, timeout: float = 8
) -> tuple[str, list[MinuteBar]]:
    base_params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ndays": "1",
        "secid": secid,
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0",
    }
    last_error: Exception | None = None
    for endpoint, token in (
        (EASTMONEY_REALTIME_TRENDS_URL, "fa5fd1943c7b386f172d6893dbfba10b"),
        (EASTMONEY_TRENDS_URL, "7eea3edcaed734bea9cbfc24409ed989"),
    ):
        params = {
            **base_params,
            "ut": token,
            "iscr": "1" if endpoint == EASTMONEY_REALTIME_TRENDS_URL else "0",
            "iscca": "0",
        }
        request = Request(f"{endpoint}?{urlencode(params)}", headers=headers)
        for _attempt in range(2):
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.load(response)
                data = payload.get("data") or {}
                bars = _parse_eastmoney_trends(data)
                if bars:
                    return str(data.get("name") or secid), bars
            except (OSError, ValueError) as exc:
                last_error = exc
                time.sleep(0.2)
    raise RuntimeError(f"{secid} 分时行情获取失败：{last_error}")


def fetch_stock_boards(code: str, timeout: float = 8) -> list[StockBoard]:
    code = normalize_stock_code(code)
    if code.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif code.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    params = {
        "type": "RPT_F10_CORETHEME_BOARDTYPE",
        "sty": (
            "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_CODE,BOARD_NAME,"
            "IS_PRECISE,BOARD_RANK,BOARD_TYPE"
        ),
        "filter": f'(SECUCODE="{code}.{suffix}")',
        "p": "1",
        "ps": "",
        "sr": "1",
        "st": "BOARD_RANK",
        "source": "HSF10",
        "client": "PC",
    }
    request = Request(
        f"{EASTMONEY_F10_URL}?{urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    last_error: Exception | None = None
    payload = None
    for _attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            break
        except (OSError, ValueError) as exc:
            last_error = exc
            time.sleep(0.2)
    if payload is None:
        raise RuntimeError(f"{code} 行业板块获取失败：{last_error}")
    rows = (payload.get("result") or {}).get("data") or []
    boards: list[StockBoard] = []
    for row in rows:
        board_type = row.get("BOARD_TYPE")
        if board_type not in ("行业", None):
            continue
        board_code = str(row["BOARD_CODE"])
        boards.append(
            StockBoard(
                name=str(row["BOARD_NAME"]),
                code=f"BK{board_code[-4:].zfill(4)}",
                kind="行业" if board_type == "行业" else "概念",
            )
        )
    if not boards:
        raise RuntimeError(f"未找到 {code} 的行业或概念板块")
    return boards


def fetch_realtime_change(code: str, timeout: float = 5) -> float:
    """Return the stock's official change versus the previous close."""
    return fetch_tencent_stock_change(code, timeout)


def default_stock_board(boards: Sequence[StockBoard]) -> StockBoard:
    for board in boards:
        if board.name == "液冷概念":
            return board
    industries = [board for board in boards if board.kind == "行业"]
    return industries[-1] if industries else boards[0]


def _context_values(bars: list[MinuteBar]) -> tuple[float, float]:
    if not bars:
        return 0.0, 0.0
    opening = bars[0].open or bars[0].close
    recent = bars[max(len(bars) - 6, 0)].close
    return bars[-1].close / opening - 1, bars[-1].close / recent - 1


def fetch_intraday_context(
    code: str,
    timeout: float = 8,
    sector: StockBoard | None = None,
    boards: list[StockBoard] | None = None,
) -> IntradayContext:
    market_code = market_index_code(code)
    market_name, market_bars = fetch_minute_bars(market_code, days=1, timeout=timeout)
    available_boards = boards if boards is not None else fetch_stock_boards(code, timeout)
    selected_sector = sector or default_stock_board(available_boards)
    fetched_sector_name, sector_bars = _fetch_eastmoney_secid_bars(
        f"90.{selected_sector.code}", timeout
    )
    market_change, market_momentum = _context_values(market_bars)
    try:
        market_change = fetch_tencent_index_change(market_code, timeout)
    except (OSError, RuntimeError, ValueError):
        pass
    sector_change, sector_momentum = _context_values(sector_bars)
    return IntradayContext(
        market_name=market_name,
        market_change=market_change,
        market_momentum=market_momentum,
        sector_name=fetched_sector_name or selected_sector.name,
        sector_change=sector_change,
        sector_momentum=sector_momentum,
    )


def fetch_minute_bars(
    code: str, days: int = 5, timeout: float = 15
) -> tuple[str, list[MinuteBar]]:
    code = normalize_stock_code(code)
    base_params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ndays": str(days),
        "secid": eastmoney_secid(code),
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0",
    }
    endpoints = (
        [EASTMONEY_REALTIME_TRENDS_URL, EASTMONEY_TRENDS_URL]
        if days == 1
        else [EASTMONEY_TRENDS_URL, EASTMONEY_REALTIME_TRENDS_URL]
    )
    payload = None
    last_error = None
    for endpoint in endpoints:
        realtime = endpoint == EASTMONEY_REALTIME_TRENDS_URL
        params = {
            **base_params,
            "ut": "fa5fd1943c7b386f172d6893dbfba10b"
            if realtime
            else "7eea3edcaed734bea9cbfc24409ed989",
            "iscr": "1" if realtime else "0",
        }
        if realtime:
            params["iscca"] = "0"
            params["ndays"] = "1"
        request = Request(f"{endpoint}?{urlencode(params)}", headers=headers)
        for attempt in range(2):
            try:
                with urlopen(request, timeout=timeout) as response:
                    candidate = json.load(response)
                if candidate.get("data", {}).get("trends"):
                    payload = candidate
                    break
            except (OSError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.4)
        if payload is not None:
            break
    if payload is None:
        try:
            return fetch_tencent_minute_bars(code, timeout=timeout)
        except (OSError, RuntimeError, ValueError) as tencent_error:
            raise RuntimeError(
                f"东方财富失败：{last_error}；腾讯失败：{tencent_error}"
            ) from tencent_error

    data = payload.get("data")
    if not data or not data.get("trends"):
        return fetch_tencent_minute_bars(code, timeout=timeout)

    bars = []
    for line in data["trends"]:
        fields = line.split(",")
        if len(fields) < 8:
            continue
        bars.append(
            MinuteBar(
                time=datetime.strptime(fields[0], "%Y-%m-%d %H:%M").replace(
                    tzinfo=CHINA_TZ
                ),
                open=float(fields[1]),
                close=float(fields[2]),
                high=float(fields[3]),
                low=float(fields[4]),
                volume=float(fields[5]),
                amount=float(fields[6]),
                average=float(fields[7]),
            )
        )
    if not bars:
        raise RuntimeError(f"{code} 的分钟行情为空")
    return str(data.get("name") or code), bars


def fetch_tencent_minute_bars(
    code: str, timeout: float = 15
) -> tuple[str, list[MinuteBar]]:
    code = normalize_stock_code(code)
    symbol = tencent_symbol(code)
    request = Request(
        f"{TENCENT_MINUTE_URL}?{urlencode({'code': symbol})}",
        headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    stock_data = payload.get("data", {}).get(symbol, {})
    minute_data = stock_data.get("data", {})
    rows = minute_data.get("data", [])
    trade_date = minute_data.get("date")
    if not rows or not trade_date:
        raise RuntimeError(f"腾讯未返回 {code} 的分钟行情")

    quote = stock_data.get("qt", {}).get(symbol, [])
    name = str(quote[1]) if len(quote) > 1 else code
    date_text = str(trade_date)
    previous_close = 0.0
    previous_volume = 0.0
    previous_amount = 0.0
    bars: list[MinuteBar] = []
    for row in rows:
        fields = str(row).split()
        if len(fields) < 4:
            continue
        close = float(fields[1])
        cumulative_volume = float(fields[2])
        cumulative_amount = float(fields[3])
        volume = max(cumulative_volume - previous_volume, 0) * 100
        amount = max(cumulative_amount - previous_amount, 0)
        average = (
            cumulative_amount / cumulative_volume / 100
            if cumulative_volume > 0
            else close
        )
        open_price = previous_close or close
        timestamp = datetime.strptime(
            f"{date_text} {fields[0]}", "%Y%m%d %H%M"
        ).replace(tzinfo=CHINA_TZ)
        bars.append(
            MinuteBar(
                time=timestamp,
                open=open_price,
                close=close,
                high=max(open_price, close),
                low=min(open_price, close),
                volume=volume,
                amount=amount,
                average=average,
            )
        )
        previous_close = close
        previous_volume = cumulative_volume
        previous_amount = cumulative_amount
    if not bars:
        raise RuntimeError(f"腾讯返回的 {code} 分钟行情为空")
    return name, bars


def fetch_sina_history_bars(
    code: str, limit: int = 1023, timeout: float = 15
) -> list[MinuteBar]:
    code = normalize_stock_code(code)
    if limit < 1 or limit > 1023:
        raise ValueError("新浪分钟历史数量必须在 1 到 1023 之间")
    if code.startswith(("4", "8", "92")):
        exchange = "bj"
    elif code.startswith(("5", "6", "9")):
        exchange = "sh"
    else:
        exchange = "sz"
    params = urlencode(
        {"symbol": f"{exchange}{code}", "scale": "1", "ma": "no", "datalen": limit}
    )
    request = Request(
        f"{SINA_MINUTE_URL}?{params}",
        headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    match = re.search(r"=\((\[.*\])\);?\s*$", raw, flags=re.DOTALL)
    if match is None:
        raise RuntimeError(f"新浪未返回 {code} 的分钟历史行情")
    payload = json.loads(match.group(1))
    bars: list[MinuteBar] = []
    for row in payload:
        volume = float(row["volume"])
        amount = float(row.get("amount") or 0)
        close = float(row["close"])
        bars.append(
            MinuteBar(
                time=datetime.strptime(row["day"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=CHINA_TZ
                ),
                open=float(row["open"]),
                close=close,
                high=float(row["high"]),
                low=float(row["low"]),
                volume=volume,
                amount=amount,
                average=amount / volume if volume > 0 and amount > 0 else close,
            )
        )
    if not bars:
        raise RuntimeError(f"新浪返回的 {code} 分钟历史行情为空")
    return bars


def completed_bars(
    bars: list[MinuteBar], now: datetime | None = None
) -> list[MinuteBar]:
    current_minute = (now or datetime.now(CHINA_TZ)).replace(second=0, microsecond=0)
    return [bar for bar in bars if bar.time < current_minute]


def calculate_macd(
    bars: list[MinuteBar], short: int = 12, long: int = 26, signal: int = 9
) -> list[MacdPoint]:
    if not bars:
        return []
    short_alpha = 2 / (short + 1)
    long_alpha = 2 / (long + 1)
    signal_alpha = 2 / (signal + 1)
    ema_short = bars[0].close
    ema_long = bars[0].close
    dea = 0.0
    points = []
    for index, bar in enumerate(bars):
        if index:
            ema_short = short_alpha * bar.close + (1 - short_alpha) * ema_short
            ema_long = long_alpha * bar.close + (1 - long_alpha) * ema_long
        dif = ema_short - ema_long
        dea = signal_alpha * dif + (1 - signal_alpha) * dea
        points.append(
            MacdPoint(
                time=bar.time,
                close=bar.close,
                dif=dif,
                dea=dea,
                histogram=2 * (dif - dea),
            )
        )
    return points


def latest_macd_signal(points: list[MacdPoint]) -> MacdSignal | None:
    if len(points) < 2:
        return None
    previous, current = points[-2:]
    if previous.histogram <= 0 < current.histogram:
        side = "BUY"
    elif previous.histogram >= 0 > current.histogram:
        side = "SELL"
    else:
        return None
    return MacdSignal(
        time=current.time,
        side=side,
        price=current.close,
        dif=current.dif,
        dea=current.dea,
        histogram=current.histogram,
    )
