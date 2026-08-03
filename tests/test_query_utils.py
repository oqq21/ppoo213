from __future__ import annotations

import unittest

import pandas as pd

from your_app.common.query_utils import (
    category_sheet_for_query,
    mask_for_item_query,
    mask_for_query,
)


class QueryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.items = pd.DataFrame({
            "itemName": [
                "쉐이드슈트",
                "쉐이드슈트 바지",
                "그린 쉐이드슈트",
                "그린 쉐이드슈트 바지",
            ]
        })

    def names(self, query: str) -> list[str]:
        return self.items.loc[
            mask_for_query(self.items, query),
            "itemName",
        ].tolist()

    def test_plain_query_contains_text(self):
        self.assertEqual(len(self.names("쉐이드슈트")), 4)

    def test_trailing_at_requires_name_to_end_there(self):
        self.assertEqual(
            self.names("쉐이드슈트@"),
            ["쉐이드슈트", "그린 쉐이드슈트"],
        )

    def test_leading_at_requires_name_to_start_there(self):
        self.assertEqual(
            self.names("@쉐이드슈트"),
            ["쉐이드슈트", "쉐이드슈트 바지"],
        )

    def test_both_ats_require_exact_name(self):
        self.assertEqual(
            self.names("@쉐이드슈트@"),
            ["쉐이드슈트"],
        )

    def test_spaces_and_special_characters_are_ignored(self):
        items = pd.DataFrame({
            "itemName": [
                "흑견랑포(여)",
                "적 견랑포 (여)",
                "흑견랑포(남)",
            ]
        })
        for query in ("견랑포여", "견랑포(여)", "견랑-포 여"):
            with self.subTest(query=query):
                self.assertEqual(
                    items.loc[mask_for_query(items, query), "itemName"].tolist(),
                    ["흑견랑포(여)", "적 견랑포 (여)"],
                )

    def test_exact_search_uses_normalized_item_name(self):
        items = pd.DataFrame({
            "itemName": ["흑 견랑포(여)", "적견랑포(여)"],
        })
        self.assertEqual(
            items.loc[
                mask_for_query(items, "@흑견랑포여@"),
                "itemName",
            ].tolist(),
            ["흑 견랑포(여)"],
        )

    def test_category_aliases_select_the_whole_item_sheet(self):
        items = pd.DataFrame({
            "itemName": ["장미꽃 귀고리", "캣츠 아이", "타임리스 문라이트", "장미꽃"],
            "sheet": ["귀고리", "귀고리", "망토", "장비_기타"],
        })
        for query in ("귀고리", "귀거리"):
            with self.subTest(query=query):
                self.assertEqual(category_sheet_for_query(query), "귀고리")
                self.assertEqual(
                    items.loc[mask_for_item_query(items, query), "itemName"].tolist(),
                    ["장미꽃 귀고리", "캣츠 아이"],
                )
        self.assertEqual(category_sheet_for_query("망토"), "망토")
        self.assertEqual(
            items.loc[mask_for_item_query(items, "망토"), "itemName"].tolist(),
            ["타임리스 문라이트"],
        )

    def test_category_alias_must_be_the_whole_query(self):
        self.assertEqual(category_sheet_for_query("장미꽃 귀고리"), "")


if __name__ == "__main__":
    unittest.main()
