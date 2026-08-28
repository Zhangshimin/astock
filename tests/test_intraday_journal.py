from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from astock_hub.intraday import CHINA_TZ
from astock_hub.intraday_journal import TradeJournal


class TradeJournalTest(unittest.TestCase):
    def test_actual_sell_survives_reload_and_opens_buyback_leg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.json"
            occurred_at = datetime(2026, 7, 29, 10, 15, tzinfo=CHINA_TZ)
            TradeJournal(path).record("002837", "SELL", 20.30, occurred_at)

            pending = TradeJournal(path).open_sell("002837", occurred_at.date())

            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.price, 20.30)
            self.assertEqual(pending.position_fraction, 0.1)
            self.assertEqual(pending.occurred_at, occurred_at)

    def test_actual_buy_closes_the_open_sell_leg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.json"
            journal = TradeJournal(path)
            sell_time = datetime(2026, 7, 29, 10, 15, tzinfo=CHINA_TZ)
            journal.record("002837", "SELL", 20.30, sell_time)
            journal.record("002837", "BUY", 20.10, sell_time.replace(hour=11))

            self.assertIsNone(journal.open_sell("002837", sell_time.date()))

    def test_actual_buy_without_sell_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.json")

            with self.assertRaisesRegex(ValueError, "尚未记录实际卖出"):
                journal.record(
                    "002837",
                    "BUY",
                    20.10,
                    datetime(2026, 7, 29, 11, 0, tzinfo=CHINA_TZ),
                )

    def test_journal_counts_completed_cycles_and_exposes_latest_trade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = TradeJournal(Path(directory) / "trades.json")
            trade_time = datetime(2026, 7, 29, 10, 0, tzinfo=CHINA_TZ)
            journal.record("002837", "SELL", 20.30, trade_time)
            latest = journal.record(
                "002837", "BUY", 20.10, trade_time.replace(hour=11)
            )

            self.assertEqual(journal.completed_cycles("002837", trade_time.date()), 1)
            self.assertEqual(journal.latest_trade("002837", trade_time.date()), latest)


if __name__ == "__main__":
    unittest.main()
