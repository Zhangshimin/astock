from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from astock_hub.forum import _extract_json_content, analyze_records, load_records, normalize_record, score_record
from astock_hub.cli import _xueqiu_symbol


class ForumSentimentTest(unittest.TestCase):
    def test_loads_xueqiu_jsonl_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xueqiu.jsonl"
            rows = [
                {"content": "公司业绩增长明显，继续看好", "like_count": 8},
                {"content": "公司业绩增长明显，继续看好", "like_count": 8},
                {"content": "已经破位，风险很大", "reply_count": 2},
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            records = load_records(path, "xueqiu")
            summary = analyze_records(records)
        self.assertEqual(summary.total_loaded, 3)
        self.assertEqual(summary.total_analyzed, 2)
        self.assertEqual(summary.duplicates_removed, 1)
        self.assertEqual(summary.bullish, 1)
        self.assertEqual(summary.bearish, 1)

    def test_normalizes_eastmoney_fields(self) -> None:
        record = normalize_record(
            {
                "comment_content": "利好落地，准备加仓",
                "comment_like": "12",
                "comment_date": "2026-07-22",
            },
            "eastmoney",
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.likes, 12)
        self.assertEqual(record.source, "eastmoney")
        self.assertEqual(score_record(record).label, "bullish")

    def test_negation_reverses_polarity(self) -> None:
        record = normalize_record({"text": "这个位置并不看好，暂时不买入"}, "xueqiu")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(score_record(record).label, "bearish")

    def test_extracts_fenced_llm_json(self) -> None:
        payload = _extract_json_content('```json\n{"items":[{"id":0,"score":0.5}]}\n```')
        self.assertEqual(payload["items"][0]["score"], 0.5)

    def test_normalizes_xueqiu_symbol(self) -> None:
        self.assertEqual(_xueqiu_symbol("600519.SH"), "SH600519")
        self.assertEqual(_xueqiu_symbol("000001.SZ"), "SZ000001")


if __name__ == "__main__":
    unittest.main()
