from __future__ import annotations

import unittest

import pandas as pd

from your_app.common.query_utils import mask_for_query


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


if __name__ == "__main__":
    unittest.main()
