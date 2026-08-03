from __future__ import annotations

import unittest

from your_app.api.client import build_params


class CategoryApiSearchTests(unittest.TestCase):
    def test_earring_category_search_has_no_item_or_level_restriction(self):
        params = build_params("귀고리", "", 0, stat_tokens=["마22"])

        self.assertEqual(params["itemType"], "earrings")
        self.assertEqual(params["lowLevel"], "")
        self.assertEqual(params["highLevel"], "")
        self.assertNotIn("itemCode", params)
        self.assertEqual(params["lowHapma"], 22)
        self.assertEqual(params["highHapma"], 22)

    def test_cape_category_search_has_no_item_or_level_restriction(self):
        params = build_params("망토", "", 0, stat_tokens=["럭5"])

        self.assertEqual(params["itemType"], "cape")
        self.assertEqual(params["lowLevel"], "")
        self.assertEqual(params["highLevel"], "")
        self.assertNotIn("itemCode", params)
        self.assertEqual(params["lowincLUK"], 5)
        self.assertEqual(params["highincLUK"], 5)


if __name__ == "__main__":
    unittest.main()
