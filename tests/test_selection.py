import unittest

import pandas as pd

from scripts.selection import knapsack


class TradeoffTests(unittest.TestCase):
    def test_knapsack_maximizes_utility(self):
        items = pd.DataFrame({"candidate_id": [1, 2, 3], "length_m": [60, 50, 40],
                              "composite_priority": [10, 9, 7]})
        self.assertEqual(set(knapsack(items, 100)), {1, 3})

    def test_knapsack_respects_whole_links(self):
        items = pd.DataFrame({"candidate_id": [1], "length_m": [101], "composite_priority": [10]})
        self.assertEqual(knapsack(items, 100), [])


if __name__ == "__main__":
    unittest.main()
