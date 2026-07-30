from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from your_app.processing.packet_store import (
    ACTUAL_STATS,
    deduplicate_active_completed,
    filter_packet_rows,
    format_packet_time,
    format_sale_duration,
    item_color_key,
    item_color_score,
    packet_view,
    search_packet_data,
)


def sample_row(**overrides):
    row = {
        "status": "active",
        "captured_at": "2026-07-20 10:00:00",
        "internal_time": "2026-07-23 10:00:00",
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
    def test_magic_and_int_conditions_use_int_plus_magic_attack(self):
        frame = pd.DataFrame([
            sample_row(add_INT=9, add_MAD=15, total_INT=109, total_MAD=115),
            sample_row(itemCode=1_000_002, add_INT=24, add_MAD=1),
        ])
        self.assertEqual(
            filter_packet_rows(frame, ["마224"])["itemCode"].tolist(),
            [1_000_001],
        )
        self.assertEqual(
            filter_packet_rows(frame, ["인224"])["itemCode"].tolist(),
            [1_000_001],
        )

    def test_zero_magic_condition_uses_combined_total_value(self):
        frame = pd.DataFrame([
            sample_row(add_INT=0, add_MAD=0),
            sample_row(itemCode=1_000_002, total_INT=1, add_INT=0),
            sample_row(itemCode=1_000_003, total_MAD=1, add_MAD=0),
        ])
        self.assertEqual(
            filter_packet_rows(frame, ["마0"])["itemCode"].tolist(),
            [1_000_001],
        )
        self.assertEqual(
            filter_packet_rows(frame, ["인0"])["itemCode"].tolist(),
            [1_000_001],
        )

    def test_positive_simple_condition_uses_total_stat(self):
        frame = pd.DataFrame([
            sample_row(total_DEX=30, base_DEX=5, add_DEX=25),
            sample_row(itemCode=1_000_002, total_DEX=25, base_DEX=0, add_DEX=25),
        ])
        result = filter_packet_rows(frame, ["덱25"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_002])

    def test_beopsin_uses_total_stats(self):
        frame = pd.DataFrame([
            sample_row(add_INT=1, add_LUK=1, add_MAD=0, total_INT=6, total_LUK=4),
            sample_row(itemCode=1_000_002, add_INT=2, add_LUK=1, add_MAD=0),
        ])
        result = filter_packet_rows(frame, ["법신10"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_beopsa_total_excludes_magic_attack(self):
        frame = pd.DataFrame([
            sample_row(total_INT=20, total_LUK=10, total_MAD=50),
            sample_row(itemCode=1_000_002, total_INT=20, total_LUK=9, total_MAD=1),
        ])
        result = filter_packet_rows(frame, ["법사30"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_zero_condition_uses_total_stat(self):
        frame = pd.DataFrame([
            sample_row(total_STR=0, base_STR=5, add_STR=-5),
            sample_row(itemCode=1_000_002, total_STR=6, base_STR=5, add_STR=1),
        ])
        result = filter_packet_rows(frame, ["힘0"])
        self.assertEqual(result["itemCode"].tolist(), [1_000_001])

    def test_hp_mp_conditions_use_total_stats(self):
        frame = pd.DataFrame([
            sample_row(
                total_HP=120,
                base_HP=100,
                add_HP=20,
                total_MP=80,
                base_MP=80,
                add_MP=0,
            ),
            sample_row(
                itemCode=1_000_002,
                total_HP=100,
                base_HP=100,
                add_HP=0,
                total_MP=90,
                base_MP=80,
                add_MP=10,
            ),
        ])
        self.assertEqual(
            filter_packet_rows(frame, ["HP120"])["itemCode"].tolist(),
            [1_000_001],
        )
        self.assertEqual(
            filter_packet_rows(frame, ["HP100"])["itemCode"].tolist(),
            [1_000_002],
        )
        self.assertEqual(
            filter_packet_rows(frame, ["MP90"])["itemCode"].tolist(),
            [1_000_002],
        )
        self.assertEqual(
            filter_packet_rows(frame, ["MP80"])["itemCode"].tolist(),
            [1_000_001],
        )

    def test_gem_toggle_adds_gem_to_total_search_stat(self):
        frame = pd.DataFrame([
            sample_row(total_PAD=110, gem_PAD=3),
            sample_row(itemCode=1_000_002, total_PAD=113, gem_PAD=3),
        ])
        self.assertEqual(
            filter_packet_rows(frame, ["공113"], include_gems=False)[
                "itemCode"
            ].tolist(),
            [1_000_002],
        )
        self.assertEqual(
            filter_packet_rows(frame, ["공113"], include_gems=True)[
                "itemCode"
            ].tolist(),
            [1_000_001],
        )

    def test_packet_view_rounds_prices_to_man_and_colors_status(self):
        frame = pd.DataFrame([
            sample_row(
                unit_price=10_005_000,
                gem_cost=2_254_999,
                recognized_gem_value=2_029_499,
                true_price=7_975_501,
                gem_options="",
            )
        ])
        result = packet_view(frame)
        self.assertEqual(result.at[0, "상태"], "🔵 Active")
        self.assertRegex(result.at[0, "패킷시간"], r"^\d+일 전$")
        self.assertEqual(result.at[0, "판매가(만)"], 1_001)
        self.assertEqual(result.at[0, "보석비(원가, 만)"], 225)
        self.assertEqual(result.at[0, "인정보석가치(90%, 만)"], 203)
        self.assertEqual(result.at[0, "찐판매가(만)"], 798)
        self.assertNotIn("총스탯", result.columns)

    def test_additional_stats_are_compact(self):
        frame = pd.DataFrame([
            sample_row(add_DEX=21, add_PDD=-1),
        ])
        result = packet_view(frame)
        self.assertEqual(result.at[0, "추가스탯"], "덱21 물방-1")

    def test_item_color_uses_additional_stats_and_hp_mp_tenths(self):
        row = pd.Series(sample_row(
            add_DEX=20,
            add_HP=29,
            add_MP=19,
        ))
        self.assertEqual(item_color_score(row), 23)
        self.assertEqual(item_color_key(item_color_score(row)), "purple")

    def test_item_color_boundaries(self):
        expected = {
            -1: "gray",
            0: "white",
            5: "white",
            6: "blue",
            22: "blue",
            23: "purple",
            39: "purple",
            40: "yellow",
            54: "yellow",
            55: "lime",
            69: "lime",
            70: "red",
        }
        for score, color in expected.items():
            with self.subTest(score=score):
                self.assertEqual(item_color_key(score), color)

    def test_gems_display_actual_stat_values(self):
        frame = pd.DataFrame([
            sample_row(
                option_codes="26008,26011,26000,16006",
                gem_options="상급 힘, 상급 민첩, 상급 공격, 중급 HP",
            ),
        ])
        result = packet_view(frame)
        self.assertEqual(result.at[0, "보석"], "힘+5, 덱+5, 공+3, HP+20")

    def test_completed_removes_matching_active_within_three_days(self):
        active = pd.DataFrame([sample_row()])
        completed = pd.DataFrame([
            sample_row(
                status="completed",
                captured_at="2026-07-22 10:00:00",
                internal_time="2026-07-21 10:00:00",
                event_time="2026-07-22 10:00:00",
            )
        ])
        result, completed_result, count = deduplicate_active_completed(
            active,
            completed,
        )
        self.assertEqual(count, 1)
        self.assertTrue(result.empty)
        self.assertEqual(
            completed_result.at[0, "_sale_duration_minutes"],
            2 * 24 * 60,
        )

    def test_completed_does_not_remove_active_after_three_days(self):
        active = pd.DataFrame([sample_row()])
        completed = pd.DataFrame([
            sample_row(
                status="completed",
                captured_at="2026-07-24 10:00:00",
                internal_time="2026-07-19 10:00:00",
                event_time="2026-07-24 10:00:00",
            )
        ])
        result, completed_result, count = deduplicate_active_completed(
            active,
            completed,
        )
        self.assertEqual(count, 0)
        self.assertEqual(len(result), 1)
        self.assertTrue(
            pd.isna(completed_result.at[0, "_sale_duration_minutes"])
        )

    def test_identical_listings_pair_with_nearest_prior_active(self):
        active = pd.DataFrame([
            sample_row(captured_at="2026-07-20 10:00:00"),
            sample_row(captured_at="2026-07-20 11:00:00"),
        ])
        completed = pd.DataFrame([
            sample_row(
                status="completed",
                captured_at="2026-07-20 10:30:00",
                internal_time="2026-07-21 10:00:00",
            ),
            sample_row(
                status="completed",
                captured_at="2026-07-20 11:40:00",
                internal_time="2026-07-21 11:00:00",
            ),
        ])
        active_result, completed_result, count = (
            deduplicate_active_completed(active, completed)
        )
        self.assertTrue(active_result.empty)
        self.assertEqual(count, 2)
        self.assertEqual(
            completed_result["_sale_duration_minutes"].tolist(),
            [30, 40],
        )

    def test_packet_time_shows_relative_time_without_parentheses(self):
        minute_row = pd.Series(sample_row(
            status="completed",
            captured_at="2026-07-20 10:32:00",
            _sale_duration_minutes=32,
        ))
        hour_row = pd.Series(sample_row(
            status="completed",
            captured_at="2026-07-20 13:05:00",
            _sale_duration_minutes=185,
        ))
        self.assertRegex(format_packet_time(minute_row), r"^\d+일 전$")
        self.assertRegex(format_packet_time(hour_row), r"^\d+일 전$")
        self.assertNotIn("(", format_packet_time(hour_row))
        self.assertEqual(format_sale_duration(minute_row), "32분")
        self.assertEqual(format_sale_duration(hour_row), "3시간 5분")


class PacketDataIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.active = cls.root / "packet_active.parquet"
        cls.completed = cls.root / "packet_completed.parquet"

    def test_dark_arund_total_attack_105_is_searchable(self):
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
