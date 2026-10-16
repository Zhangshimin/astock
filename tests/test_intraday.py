from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from io import BytesIO
from unittest.mock import patch

from astock_hub.intraday import (
    CHINA_TZ,
    GlobalMarketContext,
    IntradayContext,
    MinuteBar,
    StockBoard,
    calculate_macd,
    completed_bars,
    default_stock_board,
    fetch_sina_history_bars,
    fetch_tencent_index_change,
    fetch_tencent_minute_bars,
    fetch_tencent_stock_change,
    latest_macd_signal,
    market_index_code,
    normalize_stock_code,
    parse_equity_event_payload,
)


def bar(minute: int, close: float) -> MinuteBar:
    timestamp = datetime(2026, 7, 27, 9, tzinfo=CHINA_TZ) + timedelta(minutes=minute)
    return MinuteBar(timestamp, close, close, close, close, 1, close, close)


class IntradayMacdTest(unittest.TestCase):
    def test_global_context_turns_reference_assets_into_regime(self) -> None:
        strong = GlobalMarketContext(sp500=0.01, nasdaq=0.012, semis=0.015, bank=0.008)
        weak = GlobalMarketContext(sp500=-0.01, nasdaq=-0.012, semis=-0.015, vix=0.10, bank=-0.008)
        self.assertEqual(strong.label, "偏强")
        self.assertEqual(weak.label, "偏弱")

    def test_parses_upcoming_equity_event_and_describes_impact(self) -> None:
        payload = {
            "result": {
                "data": [
                    {
                        "SECURITY_CODE": "002837",
                        "SECURITY_NAME_ABBR": "英维克",
                        "EQUITY_RECORD_DATE": "2026-08-05 00:00:00",
                        "EX_DIVIDEND_DATE": "2026-08-06 00:00:00",
                        "PRETAX_BONUS_RMB": 1.7,
                        "IMPL_PLAN_PROFILE": "10转3股派1.70元",
                        "ASSIGN_PROGRESS": "实施分配",
                    }
                ]
            }
        }

        event = parse_equity_event_payload(
            payload, "002837", today=date(2026, 7, 31)
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.record_date.isoformat(), "2026-08-05")
        self.assertEqual(event.days_until, 5)
        self.assertIn("登记日前持有", event.impact)
        self.assertIn("10转3股派1.70元", event.plan)

    def test_selects_the_matching_benchmark_index(self) -> None:
        self.assertEqual(market_index_code("600519"), "000001")
        self.assertEqual(market_index_code("002837"), "000001")
        self.assertEqual(market_index_code("830799"), "000001")

    def test_reverse_sell_strength_uses_market_and_sector_momentum(self) -> None:
        strong = IntradayContext("大盘", 0.01, -0.001, "板块", 0.02, -0.002)
        medium = IntradayContext("大盘", 0.01, 0.001, "板块", -0.01, -0.002)
        weak = IntradayContext("大盘", 0.01, 0.001, "板块", 0.02, 0.002)

        self.assertEqual(strong.reverse_sell_strength, "强")
        self.assertEqual(medium.reverse_sell_strength, "中")
        self.assertEqual(weak.reverse_sell_strength, "弱")

    def test_liquid_cooling_is_the_preferred_context_board(self) -> None:
        boards = [
            StockBoard("专用设备", "BK0910", "行业"),
            StockBoard("液冷概念", "BK1138", "概念"),
        ]

        self.assertEqual(default_stock_board(boards).code, "BK1138")

    def test_normalizes_common_stock_code_formats(self) -> None:
        self.assertEqual(normalize_stock_code("SH600519"), "600519")
        self.assertEqual(normalize_stock_code("000001.sz"), "000001")
        with self.assertRaises(ValueError):
            normalize_stock_code("123")

    def test_excludes_the_current_unfinished_minute(self) -> None:
        bars = [bar(30, 10), bar(31, 11)]
        now = datetime(2026, 7, 27, 9, 31, 45, tzinfo=CHINA_TZ)
        self.assertEqual(completed_bars(bars, now), bars[:1])

    def test_detects_latest_golden_and_dead_cross(self) -> None:
        golden_prices = [10.0] * 35 + [10.5]
        golden = latest_macd_signal(
            calculate_macd([bar(i, price) for i, price in enumerate(golden_prices)])
        )
        self.assertIsNotNone(golden)
        assert golden is not None
        self.assertEqual(golden.side, "BUY")

        dead_prices = [10.0] * 35 + [9.5]
        dead = latest_macd_signal(
            calculate_macd([bar(i, price) for i, price in enumerate(dead_prices)])
        )
        self.assertIsNotNone(dead)
        assert dead is not None
        self.assertEqual(dead.side, "SELL")

    def test_parses_tencent_minute_fallback(self) -> None:
        payload = {
            "data": {
                "sz000001": {
                    "data": {
                        "date": "20260727",
                        "data": [
                            "0930 10.00 10 10000.00",
                            "0931 10.10 15 15050.00",
                        ],
                    },
                    "qt": {"sz000001": ["51", "测试股票"]},
                }
            }
        }
        response = BytesIO(json.dumps(payload).encode())
        with patch("astock_hub.intraday.urlopen", return_value=response):
            name, bars = fetch_tencent_minute_bars("000001")

        self.assertEqual(name, "测试股票")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[1].volume, 500)
        self.assertEqual(bars[1].amount, 5050)
        self.assertEqual(bars[1].time.tzinfo, CHINA_TZ)

    def test_uses_previous_close_for_the_market_index_change(self) -> None:
        response = BytesIO(
            'v_sz399001="51~深证成指~399001~13677.71~14148.73";'.encode("gbk")
        )
        with patch("astock_hub.intraday.urlopen", return_value=response):
            change = fetch_tencent_index_change("399001")

        self.assertAlmostEqual(change, 13677.71 / 14148.73 - 1)

    def test_uses_previous_close_for_the_stock_change(self) -> None:
        response = BytesIO(
            'v_sz002837="51~英维克~002837~55.24~61.26";'.encode("gbk")
        )
        with patch("astock_hub.intraday.urlopen", return_value=response):
            change = fetch_tencent_stock_change("002837")

        self.assertAlmostEqual(change, 55.24 / 61.26 - 1)

    def test_parses_sina_jsonp_history(self) -> None:
        payload = (
            "var _astock_history=(["
            '{"day":"2026-07-21 10:20:00","open":"10.00",'
            '"high":"10.20","low":"9.90","close":"10.10",'
            '"volume":"1000","amount":"10100"}'
            "]);"
        )
        response = BytesIO(payload.encode())
        with patch("astock_hub.intraday.urlopen", return_value=response):
            bars = fetch_sina_history_bars("002837")

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 10.1)
        self.assertEqual(bars[0].average, 10.1)


if __name__ == "__main__":
    unittest.main()
