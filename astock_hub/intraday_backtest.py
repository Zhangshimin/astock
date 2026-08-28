from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from astock_hub.intraday import MacdPoint, MacdSignal, MinuteBar, calculate_macd


@dataclass(frozen=True)
class CandidateRule:
    warmup: int
    confirmations: int
    require_vwap: bool
    require_ema: bool


@dataclass(frozen=True)
class IntradayStrategyConfig:
    """Price guards for one-tenth-position intraday reverse-T reminders."""

    sell_vwap_premium: float = 0.005
    buyback_discount: float = 0.008
    chase_back_rise: float = 0.006
    cooldown_minutes: int = 20
    max_cycles: int = 2
    sell_start: str = "09:45"
    sell_cutoff: str = "14:20"


CANDIDATE_RULES: dict[str, CandidateRule] = {
    "histogram": CandidateRule(4, 3, False, False),
    "sensitive": CandidateRule(12, 1, False, False),
    "balanced": CandidateRule(35, 2, True, False),
    "steady": CandidateRule(60, 3, True, True),
}


@dataclass(frozen=True)
class BacktestEvent:
    signal: MacdSignal
    forward_return: float | None


@dataclass(frozen=True)
class BacktestTrade:
    direction: str
    forced_exit: bool
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    return_rate: float
    position_fraction: float
    account_return: float


@dataclass(frozen=True)
class BacktestResult:
    bars: list[MinuteBar]
    events: list[BacktestEvent]
    trades: list[BacktestTrade]
    hit_rate: float
    trade_win_rate: float
    cumulative_spread_return: float
    cumulative_return: float
    max_drawdown: float
    position_fraction: float


def ema_values(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def session_vwap_values(bars: list[MinuteBar]) -> list[float]:
    result: list[float] = []
    amount_total = 0.0
    volume_total = 0.0
    current_date = None
    for bar in bars:
        if bar.time.date() != current_date:
            current_date = bar.time.date()
            amount_total = 0.0
            volume_total = 0.0
        amount_total += bar.amount
        volume_total += bar.volume
        raw_vwap = amount_total / volume_total if volume_total > 0 else bar.close
        if raw_vwap > bar.close * 10:
            raw_vwap /= 100
        result.append(raw_vwap if raw_vwap > 0 else bar.average or bar.close)
    return result


def histogram_shrink_sides(
    points: list[MacdPoint], confirmations: int = 3
) -> list[str | None]:
    """Emit one candidate when a same-color MACD bar shrinks N times in a row."""
    if confirmations < 1:
        raise ValueError("MACD柱缩短确认次数必须大于 0")
    sides: list[str | None] = [None] * len(points)
    shrink_streak = 0
    for index in range(1, len(points)):
        previous = points[index - 1].histogram
        current = points[index].histogram
        # 同色且绝对高度下降才算一次“变短”；零轴穿越必须重新累计，
        # 否则绿柱翻红时会被错误识别成绿柱继续缩短。
        same_color = (previous > 0 and current > 0) or (
            previous < 0 and current < 0
        )
        if same_color and abs(current) < abs(previous):
            shrink_streak += 1
        else:
            shrink_streak = 0
        # 仅在首次达到阈值时提醒，避免柱体持续缩短造成每分钟重复弹窗。
        if shrink_streak == confirmations:
            sides[index] = "SELL" if current > 0 else "BUY"
    return sides


def candidate_sides(
    bars: list[MinuteBar], points: list[MacdPoint], mode: str
) -> list[str | None]:
    if not points:
        return []
    if mode == "histogram":
        return histogram_shrink_sides(points)
    rule = CANDIDATE_RULES[mode]
    closes = [point.close for point in points]
    ema20 = ema_values(closes, 20)
    vwap = session_vwap_values(bars)
    sides: list[str | None] = [None] * len(points)
    for index in range(rule.warmup - 1, len(points)):
        minute = points[index].time.strftime("%H:%M")
        if not "09:45" <= minute <= "14:45":
            continue
        cross_index = index - rule.confirmations + 1
        if cross_index < 1:
            continue
        previous = points[cross_index - 1]
        crossed = points[cross_index]
        window = points[cross_index : index + 1]
        if previous.histogram <= 0 < crossed.histogram and all(
            point.histogram > 0 for point in window
        ):
            side = "BUY"
        elif previous.histogram >= 0 > crossed.histogram and all(
            point.histogram < 0 for point in window
        ):
            side = "SELL"
        else:
            continue
        if rule.require_vwap and (
            (side == "BUY" and closes[index] > vwap[index])
            or (side == "SELL" and closes[index] < vwap[index])
        ):
            continue
        if rule.require_ema and (
            (side == "BUY" and ema20[index] <= ema20[index - 1])
            or (side == "SELL" and ema20[index] >= ema20[index - 1])
        ):
            continue
        sides[index] = side
    return sides


def reverse_t_action_sides(
    sides: Sequence[str | None], force_buy_back_index: int | None = None
) -> list[str | None]:
    """Keep only executable reverse-T actions: sell first, then buy back."""
    actions: list[str | None] = [None] * len(sides)
    waiting_to_buy_back = False
    for index, side in enumerate(sides):
        if side == "SELL" and not waiting_to_buy_back:
            actions[index] = side
            waiting_to_buy_back = True
        elif side == "BUY" and waiting_to_buy_back:
            actions[index] = side
            waiting_to_buy_back = False
    if waiting_to_buy_back and force_buy_back_index is not None:
        if not 0 <= force_buy_back_index < len(actions):
            raise IndexError("强制买回位置超出候选序列")
        actions[force_buy_back_index] = "BUY"
    return actions


def guarded_reverse_t_action_sides(
    bars: list[MinuteBar],
    points: list[MacdPoint],
    sides: Sequence[str | None],
    config: IntradayStrategyConfig | None = None,
    force_buy_back_index: int | None = None,
) -> list[str | None]:
    """Apply price, time, cooldown and cycle guards to reverse-T candidates."""
    if len(bars) != len(points) or len(points) != len(sides):
        raise ValueError("分钟线、MACD点和候选序列长度必须一致")
    strategy = config or IntradayStrategyConfig()
    vwap = session_vwap_values(bars)
    actions: list[str | None] = [None] * len(points)
    pending_sell_price: float | None = None
    last_buy_time: datetime | None = None
    completed_cycles = 0
    for index, (point, side) in enumerate(zip(points, sides, strict=True)):
        minute = point.time.strftime("%H:%M")
        if pending_sell_price is None:
            cooldown_over = last_buy_time is None or point.time - last_buy_time >= timedelta(
                minutes=strategy.cooldown_minutes
            )
            # 卖出必须同时处于可开仓时段、站上日内均价门槛，并且当天轮次未用完。
            # 这样不会因为一个孤立的 MACD 柱缩信号就在低位卖出底仓。
            if (
                side == "SELL"
                and strategy.sell_start <= minute <= strategy.sell_cutoff
                and point.close >= vwap[index] * (1 + strategy.sell_vwap_premium)
                and cooldown_over
                and completed_cycles < strategy.max_cycles
            ):
                actions[index] = "SELL"
                pending_sell_price = point.close
            continue

        reached_target = point.close <= pending_sell_price * (
            1 - strategy.buyback_discount
        )
        reached_chase = point.close >= pending_sell_price * (
            1 + strategy.chase_back_rise
        )
        forced = force_buy_back_index is not None and index >= force_buy_back_index
        # 正常买回要求绿柱缩短信号与目标价差同时成立；卖飞保护和尾盘回补属于
        # 风险控制，无须等待技术指标，否则可能持续扩大回补成本。
        if (side == "BUY" and reached_target) or reached_chase or forced:
            actions[index] = "BUY"
            pending_sell_price = None
            last_buy_time = point.time
            completed_cycles += 1
    return actions


def actual_buyback_reminder_sides(
    points: list[MacdPoint],
    sides: Sequence[str | None],
    sell_price: float,
    sell_time: datetime,
    config: IntradayStrategyConfig | None = None,
    force_buy_back_index: int | None = None,
) -> list[str | None]:
    """Build one buyback reminder from a manually confirmed sell fill."""
    if len(points) != len(sides):
        raise ValueError("MACD点和候选序列长度必须一致")
    if sell_price <= 0:
        raise ValueError("实际卖出价格必须大于 0")
    strategy = config or IntradayStrategyConfig()
    actions: list[str | None] = [None] * len(points)
    for index, (point, side) in enumerate(zip(points, sides, strict=True)):
        # 成交发生前的历史柱不能参与后续提醒，否则重启恢复时可能匹配到旧信号。
        if point.time <= sell_time:
            continue
        reached_target = point.close <= sell_price * (1 - strategy.buyback_discount)
        reached_chase = point.close >= sell_price * (1 + strategy.chase_back_rise)
        forced = force_buy_back_index is not None and index >= force_buy_back_index
        if (side == "BUY" and reached_target) or reached_chase or forced:
            actions[index] = "BUY"
            break
    return actions


def run_backtest(
    bars: list[MinuteBar],
    mode: str,
    forward_bars: int = 15,
    cost_rate: float = 0.001,
    position_fraction: float = 0.1,
) -> BacktestResult:
    if not 0 < position_fraction <= 1:
        raise ValueError("单次仓位比例必须大于 0 且不超过 1")
    events: list[BacktestEvent] = []
    trades: list[BacktestTrade] = []
    dates = sorted({bar.time.date() for bar in bars})
    for trade_date in dates:
        session_bars = [bar for bar in bars if bar.time.date() == trade_date]
        points = calculate_macd(session_bars)
        raw_sides = candidate_sides(session_bars, points, mode)
        # 默认柱缩策略的回测必须与实时提醒共用价格保护，否则回测结果会把
        # 实盘不会提示的低位卖出、微小价差也统计成有效交易。
        sides = (
            guarded_reverse_t_action_sides(session_bars, points, raw_sides)
            if mode == "histogram"
            else reverse_t_action_sides(raw_sides)
        )
        pending_signal: MacdSignal | None = None
        for index, side in enumerate(sides):
            if side is None:
                continue
            point = points[index]
            future_index = index + forward_bars
            forward_return = None
            if future_index < len(points):
                raw_return = points[future_index].close / point.close - 1
                forward_return = raw_return if side == "BUY" else -raw_return
            signal = MacdSignal(
                time=point.time,
                side=side,
                price=point.close,
                dif=point.dif,
                dea=point.dea,
                histogram=point.histogram,
            )
            events.append(BacktestEvent(signal, forward_return))
            if side == "SELL":
                pending_signal = signal
            elif pending_signal is not None:
                return_rate = pending_signal.price / signal.price - 1 - cost_rate
                trades.append(
                    BacktestTrade(
                        direction="REVERSE_T",
                        forced_exit=False,
                        entry_time=pending_signal.time,
                        exit_time=signal.time,
                        entry_price=pending_signal.price,
                        exit_price=signal.price,
                        return_rate=return_rate,
                        position_fraction=position_fraction,
                        account_return=return_rate * position_fraction,
                    )
                )
                pending_signal = None
        if pending_signal is not None:
            close_point = points[-1]
            return_rate = pending_signal.price / close_point.close - 1 - cost_rate
            trades.append(
                BacktestTrade(
                    direction="REVERSE_T",
                    forced_exit=True,
                    entry_time=pending_signal.time,
                    exit_time=close_point.time,
                    entry_price=pending_signal.price,
                    exit_price=close_point.close,
                    return_rate=return_rate,
                    position_fraction=position_fraction,
                    account_return=return_rate * position_fraction,
                )
            )

    measured = [
        event.forward_return for event in events if event.forward_return is not None
    ]
    hit_rate = sum(value > 0 for value in measured) / len(measured) if measured else 0.0
    trade_win_rate = (
        sum(trade.return_rate > 0 for trade in trades) / len(trades) if trades else 0.0
    )
    spread_equity = 1.0
    equity = 1.0
    equity_peak = 1.0
    max_drawdown = 0.0
    for trade in trades:
        spread_equity *= 1 + trade.return_rate
        equity *= 1 + trade.account_return
        equity_peak = max(equity_peak, equity)
        max_drawdown = max(max_drawdown, (equity_peak - equity) / equity_peak)
    return BacktestResult(
        bars=bars,
        events=events,
        trades=trades,
        hit_rate=hit_rate,
        trade_win_rate=trade_win_rate,
        cumulative_spread_return=spread_equity - 1,
        cumulative_return=equity - 1,
        max_drawdown=max_drawdown,
        position_fraction=position_fraction,
    )
