from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from your_app.processing.packet_store import (
    ACTUAL_STATS,
    deduplicate_active_completed,
    filter_packet_rows,
    search_packet_data,
)


def sample_row(**overrides):
    row = {
        "status": "active",
        "captured_at": "2026-07-20 10:00:00",
        "event_time": "2026-07-20 10:00:00",
        "itemCode": 1_000_001,
        "itemName": "테스트 장비",
        "quantity": 1,
        "total_price": 10_000_000,
        "unit_price": 10_000_000,
        "total_upgrade_left": 0,
        "total_work_count": 5,
        "option_codes": "",
    }
    for stat in ACTUAL_STATS:
        row[f"total_{stat}"] = 0
        row[f"base_{stat}"] = 0
        row[f"add_{stat}"] = 0
    row.update(overrides)
    return row


class PacketRuleTests(unittest.TestCase):
    def test_positive_simple_condition_uses_total_stat(self):
        frame = pd.DataFrame([
            sample_row(total_DEX=30, base_DEX=5, add_DEX=25),
            sample_row(itemCode=1_000_002, total_DEX=25, base_DEX=0, add_DEX=25),
        ])
        result = filter_packet_rows(frame, ["덱30"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_beopsin_uses_additional_stats(self):
        frame = pd.DataFrame([
            sample_row(add_INT=1, add_LUK=1, add_MAD=0, total_INT=6, total_LUK=4),
            sample_row(itemCode=1_000_002, add_INT=2, add_LUK=1, add_MAD=0),
        ])
        result = filter_packet_rows(frame, ["법신2"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_beopsa_total_excludes_magic_attack(self):
        frame = pd.DataFrame([
            sample_row(total_INT=20, total_LUK=10, total_MAD=50),
            sample_row(itemCode=1_000_002, total_INT=20, total_LUK=9, total_MAD=1),
        ])
        result = filter_packet_rows(frame, ["법사30"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_zero_condition_uses_additional_stat(self):
        frame = pd.DataFrame([
            sample_row(total_STR=5, base_STR=5, add_STR=0),
            sample_row(itemCode=1_000_002, total_STR=6, base_STR=5, add_STR=1),
        ])
        result = filter_packet_rows(frame, ["힘0"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_completed_removes_matching_active_within_three_days(self):
        active = pd.DataFrame([sample_row()])
        completed = pd.DataFrame([
            sample_row(
                status="completed",
                captured_at="2026-07-22 10:00:00",
                event_time="2026-07-22 10:00:00",
            )
        ])
        result, count = deduplicate_active_completed(active, completed)
        self.assertEqual(count, 1)
        self.assertTrue(result.empty)

    def test_completed_does_not_remove_active_after_three_days(self):
        active = pd.DataFrame([sample_row()])
        completed = pd.DataFrame([
            sample_row(
                status="completed",
                captured_at="2026-07-24 10:00:00",
                event_time="2026-07-24 10:00:00",
            )
        ])
        result, count = deduplicate_active_completed(active, completed)
        self.assertEqual(count, 0)
        self.assertEqual(len(result), 1)


class PacketDataIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.active = cls.root / "packet_active.parquet"
        cls.completed = cls.root / "packet_completed.parquet"

    def test_dark_arund_attack_20_uses_total_attack_105(self):
        if not self.active.exists() or not self.completed.exists():
            self.skipTest("packet parquet files are not generated")
        result, _ = search_packet_data(
            self.active,
            self.completed,
            [1_452_015],
            ["공105"],
        )
        self.assertFalse(result.empty)
        self.assertTrue(result["total_PAD"].eq(105).all())


if __name__ == "__main__":
    unittest.main()
