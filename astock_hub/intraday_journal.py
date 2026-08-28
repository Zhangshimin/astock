from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from astock_hub.intraday import CHINA_TZ, normalize_stock_code

TradeSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class ActualTrade:
    code: str
    side: TradeSide
    price: float
    occurred_at: datetime
    position_fraction: float = 0.1


class TradeJournal:
    """Persist manually confirmed fills used by the reminder state machine."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> list[ActualTrade]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return [
            ActualTrade(
                code=normalize_stock_code(item["code"]),
                side=item["side"],
                price=float(item["price"]),
                occurred_at=datetime.fromisoformat(item["occurred_at"]),
                position_fraction=float(item.get("position_fraction", 0.1)),
            )
            for item in payload
        ]

    def _save(self, trades: list[ActualTrade]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = []
        for trade in trades:
            item = asdict(trade)
            item["occurred_at"] = trade.occurred_at.isoformat()
            payload.append(item)
        # 先写临时文件再替换，避免程序或系统在写入中途退出后损坏全部成交记录。
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def open_sell(self, code: str, trade_date: date | None = None) -> ActualTrade | None:
        code = normalize_stock_code(code)
        target_date = trade_date or datetime.now(CHINA_TZ).date()
        pending: ActualTrade | None = None
        for trade in self._load():
            if trade.code != code or trade.occurred_at.date() != target_date:
                continue
            if trade.side == "SELL":
                pending = trade
            elif pending is not None:
                pending = None
        return pending

    def latest_trade(
        self, code: str, trade_date: date | None = None
    ) -> ActualTrade | None:
        code = normalize_stock_code(code)
        target_date = trade_date or datetime.now(CHINA_TZ).date()
        matching = [
            trade
            for trade in self._load()
            if trade.code == code and trade.occurred_at.date() == target_date
        ]
        return matching[-1] if matching else None

    def completed_cycles(self, code: str, trade_date: date | None = None) -> int:
        code = normalize_stock_code(code)
        target_date = trade_date or datetime.now(CHINA_TZ).date()
        # record() 已保证 BUY 必须和同日未平的 SELL 配对，因此买回记录数就是完成轮次。
        return sum(
            trade.side == "BUY"
            for trade in self._load()
            if trade.code == code and trade.occurred_at.date() == target_date
        )

    def record(
        self,
        code: str,
        side: TradeSide,
        price: float,
        occurred_at: datetime | None = None,
        position_fraction: float = 0.1,
    ) -> ActualTrade:
        code = normalize_stock_code(code)
        if side not in ("BUY", "SELL"):
            raise ValueError("成交方向必须是 BUY 或 SELL")
        if price <= 0:
            raise ValueError("实际成交价格必须大于 0")
        if not 0 < position_fraction <= 1:
            raise ValueError("实际成交仓位必须大于 0 且不超过 1")
        timestamp = occurred_at or datetime.now(CHINA_TZ)
        pending = self.open_sell(code, timestamp.date())
        if side == "BUY" and pending is None:
            raise ValueError("尚未记录实际卖出，不能记录买回")
        if side == "SELL" and pending is not None:
            raise ValueError("已有待买回的实际卖出记录")
        trade = ActualTrade(code, side, price, timestamp, position_fraction)
        trades = self._load()
        trades.append(trade)
        self._save(trades)
        return trade
