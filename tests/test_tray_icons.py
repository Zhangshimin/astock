from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from astock_hub.intraday import (
    CHINA_TZ,
    MacdPoint,
    MacdSignal,
    MinuteBar,
    parse_tencent_order_book,
)
from astock_hub.intraday_backtest import (
    IntradayStrategyConfig,
    actual_buyback_reminder_sides,
    guarded_reverse_t_action_sides,
    histogram_shrink_sides,
    reverse_t_action_sides,
    run_backtest,
)
from tools.intraday_macd_alert import MacdTaskbarBar, MacdTray


def points() -> list[MacdPoint]:
    start = datetime(2026, 7, 27, 9, 30, tzinfo=CHINA_TZ)
    return [
        MacdPoint(
            time=start + timedelta(minutes=index),
            close=10 + index * 0.01,
            dif=(index - 4) * 0.001,
            dea=(index - 5) * 0.0008,
            histogram=(index - 5) * 0.0004,
        )
        for index in range(10)
    ]


class TrayIconTest(unittest.TestCase):
    @staticmethod
    def _order_book_payload(
        *,
        last: float = 10.0,
        bid1: float = 10.0,
        ask1: float = 10.01,
        upper: float = 11.0,
        lower: float = 9.0,
    ) -> str:
        fields = [""] * 49
        fields[1] = "测试股份"
        fields[2] = "000001"
        fields[3] = f"{last:.2f}"
        for level in range(5):
            fields[9 + level * 2] = f"{bid1 - level * 0.01:.2f}"
            fields[10 + level * 2] = str(1200 + level * 100)
            fields[19 + level * 2] = f"{ask1 + level * 0.01:.2f}"
            fields[20 + level * 2] = str(2100 + level * 100)
        fields[30] = "20260825103045"
        fields[47] = f"{upper:.2f}"
        fields[48] = f"{lower:.2f}"
        return f'v_sz000001="{"~".join(fields)}";'

    def test_tencent_order_book_parses_five_bid_and_ask_levels(self) -> None:
        book = parse_tencent_order_book(self._order_book_payload(), "000001")

        self.assertEqual(book.name, "测试股份")
        self.assertEqual(len(book.bids), 5)
        self.assertEqual(len(book.asks), 5)
        self.assertEqual(
            (book.bids[0].price, book.bids[0].volume_lots), (10.0, 1200)
        )
        self.assertEqual(
            (book.asks[4].price, book.asks[4].volume_lots), (10.05, 2500)
        )
        self.assertEqual(book.bids[0].volume_shares, 120_000)
        self.assertEqual(book.quote_time.strftime("%H:%M:%S"), "10:30:45")

    def test_order_book_reports_limit_up_and_limit_down_sealed_volume(self) -> None:
        limit_up = parse_tencent_order_book(
            self._order_book_payload(last=11.0, bid1=11.0, ask1=0.0), "000001"
        )
        limit_down = parse_tencent_order_book(
            self._order_book_payload(last=9.0, bid1=0.0, ask1=9.0), "000001"
        )
        normal = parse_tencent_order_book(self._order_book_payload(), "000001")

        self.assertEqual(limit_up.sealed_side, "UP")
        self.assertEqual(limit_up.sealed_volume_lots, 1200)
        self.assertEqual(limit_down.sealed_side, "DOWN")
        self.assertEqual(limit_down.sealed_volume_lots, 2100)
        self.assertIsNone(normal.sealed_side)
        self.assertEqual(normal.sealed_volume_lots, 0)

    def test_order_book_volume_formats_for_compact_display(self) -> None:
        self.assertEqual(MacdTaskbarBar._format_lots(12_345), "1.23万")
        self.assertEqual(MacdTaskbarBar._format_shares(12_345), "123.45万股")
        self.assertEqual(MacdTaskbarBar._format_inline_lots(9_999), "9999")
        self.assertEqual(MacdTaskbarBar._format_inline_lots(12_345), "1.2万")
        self.assertEqual(MacdTaskbarBar._format_inline_lots(123_456), "12万")

    def test_taskbar_is_visible_from_09_to_1530(self) -> None:
        self.assertFalse(
            MacdTaskbarBar._is_display_time(
                datetime(2026, 8, 3, 8, 59, tzinfo=CHINA_TZ)
            )
        )
        self.assertTrue(
            MacdTaskbarBar._is_display_time(
                datetime(2026, 8, 3, 9, 0, tzinfo=CHINA_TZ)
            )
        )
        self.assertTrue(
            MacdTaskbarBar._is_display_time(
                datetime(2026, 8, 3, 15, 29, tzinfo=CHINA_TZ)
            )
        )
        self.assertFalse(
            MacdTaskbarBar._is_display_time(
                datetime(2026, 8, 3, 15, 30, tzinfo=CHINA_TZ)
            )
        )

    @staticmethod
    def _strategy_series(
        prices: list[float], start_minute: int = 0
    ) -> tuple[list[MinuteBar], list[MacdPoint]]:
        start = datetime(2026, 7, 27, 10, start_minute, tzinfo=CHINA_TZ)
        source = [
            MacdPoint(start + timedelta(minutes=index), price, 0, 0, 0.01)
            for index, price in enumerate(prices)
        ]
        # 固定成交均价为 10 元，方便明确验证 VWAP 上方 0.5% 的卖出门槛。
        bars = [
            MinuteBar(point.time, point.close, point.close, point.close, point.close, 100, 1000, 10)
            for point in source
        ]
        return bars, source

    def test_guarded_strategy_requires_vwap_sell_and_target_buy_spread(self) -> None:
        bars, source = self._strategy_series([10.04, 10.06, 10.01, 9.97])
        candidates = ["SELL", "SELL", "BUY", "BUY"]

        self.assertEqual(
            guarded_reverse_t_action_sides(bars, source, candidates),
            [None, "SELL", None, "BUY"],
        )

    def test_guarded_strategy_chases_back_when_price_rises_after_sell(self) -> None:
        bars, source = self._strategy_series([10.06, 10.08, 10.13])
        candidates = ["SELL", None, None]

        self.assertEqual(
            guarded_reverse_t_action_sides(bars, source, candidates),
            ["SELL", None, "BUY"],
        )

    def test_guarded_strategy_limits_cycles_and_observes_cooldown(self) -> None:
        prices = [10.06, 9.97, 10.06, 9.97, 10.06, 9.97, 10.06]
        bars, source = self._strategy_series(prices)
        candidates = ["SELL", "BUY", "SELL", "BUY", "SELL", "BUY", "SELL"]
        config = IntradayStrategyConfig(cooldown_minutes=2, max_cycles=2)

        self.assertEqual(
            guarded_reverse_t_action_sides(bars, source, candidates, config),
            ["SELL", "BUY", None, None, "SELL", "BUY", None],
        )

    def test_actual_sell_price_drives_subsequent_buyback_reminder(self) -> None:
        _bars, source = self._strategy_series([20.30, 20.22, 20.10])
        candidates = [None, "BUY", "BUY"]

        self.assertEqual(
            actual_buyback_reminder_sides(
                source,
                candidates,
                sell_price=20.30,
                sell_time=source[0].time,
            ),
            [None, None, "BUY"],
        )

    def test_histogram_backtest_uses_the_same_price_guards_as_live_mode(self) -> None:
        bars, source = self._strategy_series([10.04, 10.06, 10.01, 9.97])
        candidates = ["SELL", "SELL", "BUY", "BUY"]

        with (
            patch("astock_hub.intraday_backtest.calculate_macd", return_value=source),
            patch(
                "astock_hub.intraday_backtest.candidate_sides",
                return_value=candidates,
            ),
        ):
            result = run_backtest(bars, "histogram", forward_bars=1)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_price, 10.06)
        self.assertEqual(result.trades[0].exit_price, 9.97)

    def test_three_green_histogram_shrinks_emit_one_buy_candidate(self) -> None:
        source = points()
        histograms = [-0.05, -0.04, -0.03, -0.02, -0.01]
        source = [
            MacdPoint(point.time, point.close, point.dif, point.dea, histogram)
            for point, histogram in zip(source, histograms, strict=False)
        ]

        self.assertEqual(
            histogram_shrink_sides(source),
            [None, None, None, "BUY", None],
        )

    def test_three_red_histogram_shrinks_emit_one_sell_candidate(self) -> None:
        source = points()
        histograms = [0.05, 0.04, 0.03, 0.02, 0.01]
        source = [
            MacdPoint(point.time, point.close, point.dif, point.dea, histogram)
            for point, histogram in zip(source, histograms, strict=False)
        ]

        self.assertEqual(
            histogram_shrink_sides(source),
            [None, None, None, "SELL", None],
        )

    def test_histogram_shrink_streak_resets_when_color_or_direction_changes(self) -> None:
        source = points()
        histograms = [-0.05, -0.04, -0.045, -0.03, -0.02]
        source = [
            MacdPoint(point.time, point.close, point.dif, point.dea, histogram)
            for point, histogram in zip(source, histograms, strict=False)
        ]

        self.assertEqual(histogram_shrink_sides(source), [None] * len(source))

    def test_taskbar_ema_uses_standard_recursive_weighting(self) -> None:
        values = MacdTaskbarBar._ema([10.0, 12.0, 11.0], period=3)

        self.assertEqual(values, [10.0, 11.0, 11.0])

    def test_market_colors_follow_a_share_convention(self) -> None:
        self.assertEqual(MacdTaskbarBar._market_color(10, 11), "#e45b65")
        self.assertEqual(MacdTaskbarBar._market_color(11, 10), "#36c98f")
        self.assertEqual(MacdTaskbarBar._market_color(10, 10), "#aeb6c2")
        self.assertEqual(MacdTaskbarBar._change_color(0.01), "#e45b65")
        self.assertEqual(MacdTaskbarBar._change_color(-0.01), "#36c98f")
        self.assertEqual(MacdTaskbarBar._change_color(0), "#929aa8")

    def test_histogram_alert_reason_is_explicit(self) -> None:
        self.assertEqual(
            MacdTaskbarBar._signal_reason("BUY", "histogram"),
            "绿柱连续3次变短",
        )
        self.assertEqual(
            MacdTaskbarBar._signal_reason("SELL", "histogram"),
            "红柱连续3次变短",
        )

    def test_actual_buyback_alert_distinguishes_target_and_chase(self) -> None:
        self.assertEqual(
            MacdTaskbarBar._signal_reason(
                "BUY", "histogram", price=20.10, sell_price=20.30
            ),
            "价差达标+绿柱缩短",
        )
        self.assertEqual(
            MacdTaskbarBar._signal_reason(
                "BUY", "histogram", price=20.43, sell_price=20.30
            ),
            "卖飞保护买回",
        )
        self.assertEqual(
            MacdTaskbarBar._signal_reason("SELL", "histogram", guarded=False),
            "红柱连续3次变短·未达VWAP高位",
        )
        self.assertEqual(
            MacdTaskbarBar._signal_reason("BUY", "histogram", guarded=False),
            "绿柱连续3次变短·未记录卖出",
        )

    def test_candidate_presets_apply_confirmation_and_trend_filters(self) -> None:
        start = datetime(2026, 7, 27, 9, 30, tzinfo=CHINA_TZ)
        source = [
            MacdPoint(
                time=start + timedelta(minutes=index),
                close=10 + index * 0.01,
                dif=0.01,
                dea=0.0,
                histogram=-0.01 if index < 60 else 0.01,
            )
            for index in range(80)
        ]
        bars = [
            MinuteBar(
                time=point.time,
                open=point.close,
                close=point.close,
                high=point.close,
                low=point.close,
                volume=100,
                amount=(point.close + 1) * 100,
                average=point.close + 1,
            )
            for point in source
        ]

        sensitive = MacdTaskbarBar._candidate_sides(bars, source, "sensitive")
        balanced = MacdTaskbarBar._candidate_sides(bars, source, "balanced")
        steady = MacdTaskbarBar._candidate_sides(bars, source, "steady")

        self.assertEqual(sensitive[60], "BUY")
        self.assertEqual(balanced[61], "BUY")
        self.assertEqual(steady[62], "BUY")

    def test_histogram_candidate_mode_uses_three_shrink_rule(self) -> None:
        start = datetime(2026, 7, 27, 10, 0, tzinfo=CHINA_TZ)
        histograms = [0.05, 0.04, 0.03, 0.02]
        source = [
            MacdPoint(start + timedelta(minutes=index), 10, 0.01, 0, histogram)
            for index, histogram in enumerate(histograms)
        ]
        bars = [
            MinuteBar(point.time, 10, 10, 10, 10, 100, 1000, 10)
            for point in source
        ]

        self.assertEqual(
            MacdTaskbarBar._candidate_sides(bars, source, "histogram"),
            [None, None, None, "SELL"],
        )

    def test_reverse_t_ignores_buy_signal_until_a_sell_occurs(self) -> None:
        start = datetime(2026, 7, 27, 9, 30, tzinfo=CHINA_TZ)
        bars = [
            MinuteBar(
                start + timedelta(minutes=index),
                10 + index * 0.01,
                10 + index * 0.01,
                10 + index * 0.01,
                10 + index * 0.01,
                100,
                (10 + index * 0.01) * 100,
                10 + index * 0.01,
            )
            for index in range(40)
        ]
        sides: list[str | None] = [None] * len(bars)
        sides[10] = "BUY"
        sides[20] = "SELL"
        sides[30] = "BUY"

        with patch("astock_hub.intraday_backtest.candidate_sides", return_value=sides):
            result = run_backtest(bars, "sensitive", forward_bars=5)

        self.assertEqual(len(result.events), 2)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.events[0].signal.side, "SELL")
        self.assertEqual(result.events[1].signal.side, "BUY")
        self.assertEqual(result.trades[0].direction, "REVERSE_T")
        self.assertFalse(result.trades[0].forced_exit)

    def test_reverse_t_action_sequence_never_buys_first_or_sells_twice(self) -> None:
        sides = ["BUY", "SELL", "SELL", "BUY", "BUY", "SELL"]

        self.assertEqual(
            reverse_t_action_sides(sides),
            [None, "SELL", None, "BUY", None, "SELL"],
        )

    def test_reverse_t_can_force_the_open_leg_to_buy_back(self) -> None:
        sides: list[str | None] = [None, "SELL", None, None]

        self.assertEqual(
            reverse_t_action_sides(sides, force_buy_back_index=3),
            [None, "SELL", None, "BUY"],
        )

    def test_backtest_supports_reverse_t_and_never_pairs_across_days(self) -> None:
        start = datetime(2026, 7, 27, 9, 30, tzinfo=CHINA_TZ)
        bars = [
            MinuteBar(
                start + timedelta(days=day, minutes=index),
                10,
                10 + index * 0.01,
                10 + index * 0.01,
                10,
                100,
                1000,
                10,
            )
            for day in range(2)
            for index in range(30)
        ]
        first_day: list[str | None] = [None] * 30
        first_day[10] = "SELL"
        first_day[20] = "BUY"
        second_day: list[str | None] = [None] * 30
        second_day[5] = "SELL"

        with patch(
            "astock_hub.intraday_backtest.candidate_sides",
            side_effect=[first_day, second_day],
        ):
            result = run_backtest(bars, "sensitive", forward_bars=5)

        self.assertEqual(len(result.trades), 2)
        self.assertEqual(result.trades[0].direction, "REVERSE_T")
        self.assertFalse(result.trades[0].forced_exit)
        self.assertTrue(result.trades[1].forced_exit)
        self.assertEqual(result.trades[0].position_fraction, 0.1)
        self.assertAlmostEqual(
            result.trades[0].account_return,
            result.trades[0].return_rate * 0.1,
        )
        self.assertEqual(
            result.trades[0].entry_time.date(), result.trades[0].exit_time.date()
        )
        self.assertEqual(
            result.trades[1].entry_time.date(), result.trades[1].exit_time.date()
        )

    def test_strong_alert_uses_four_note_sequence(self) -> None:
        with (
            patch("tools.intraday_macd_alert.winsound.Beep") as beep,
            patch("tools.intraday_macd_alert.time.sleep"),
        ):
            MacdTaskbarBar._play_alert_sound("BUY")
            buy_frequencies = [call.args[0] for call in beep.call_args_list]
            beep.reset_mock()
            MacdTaskbarBar._play_alert_sound("SELL")
            sell_frequencies = [call.args[0] for call in beep.call_args_list]

        self.assertEqual(len(buy_frequencies), 4)
        self.assertEqual(buy_frequencies, sorted(buy_frequencies))
        self.assertEqual(sell_frequencies, sorted(sell_frequencies, reverse=True))

    def test_price_macd_and_signal_icons_are_distinct(self) -> None:
        source = points()
        price = MacdTray._create_market_icon(source, "price", "bull")
        macd = MacdTray._create_market_icon(source, "macd", "bull")
        buy = MacdTray._create_signal_icon("BUY")
        sell = MacdTray._create_signal_icon("SELL")

        self.assertEqual(price.size, (64, 64))
        self.assertNotEqual(price.tobytes(), macd.tobytes())
        self.assertNotEqual(buy.tobytes(), sell.tobytes())

    def test_signal_is_highlighted_and_kept_in_menu(self) -> None:
        tray = MacdTray("000001", 10, False)
        tray.points = points()
        signal = MacdSignal(
            time=tray.points[-1].time,
            side="BUY",
            price=10.09,
            dif=0.005,
            dea=0.004,
            histogram=0.002,
        )

        tray._notify_signal(signal)

        self.assertEqual(tray.signal_side, "BUY")
        self.assertIn("买入观察", tray.last_signal)
        self.assertGreater(tray.signal_until, 0)


if __name__ == "__main__":
    unittest.main()
