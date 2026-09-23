import unittest

import pandas as pd

from scripts.shortlist import counterfactual_cost, greedy_select


class ShortlistTests(unittest.TestCase):
    def test_counterfactual_uses_better_direction(self):
        self.assertEqual(counterfactual_cost(100, 10, 80, 20, 20, 90, 10), 40)

    def test_counterfactual_never_worsens_baseline(self):
        self.assertEqual(counterfactual_cost(100, 80, 80, 40, 40, 80, 80), 100)

    def test_greedy_respects_budget(self):
        items = pd.DataFrame({"candidate_id": [1, 2, 3], "length_m": [60, 50, 30],
                              "exact_population_minutes_saved": [600, 400, 90],
                              "exact_benefit_per_m": [10, 8, 3]})
        selected = greedy_select(items, 100)
        self.assertLessEqual(selected.length_m.sum(), 100)
        self.assertEqual(set(selected.candidate_id), {1, 3})


if __name__ == "__main__":
    unittest.main()
