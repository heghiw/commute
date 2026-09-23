import unittest

import pandas as pd

from scripts.baseline import proportional_allocation


class AllocationTests(unittest.TestCase):
    def test_total_is_preserved(self):
        allocated = proportional_allocation(1000, pd.Series([1, 2, 7]))
        self.assertAlmostEqual(allocated.sum(), 1000)

    def test_area_proportions_are_respected(self):
        allocated = proportional_allocation(100, pd.Series([1, 3]))
        self.assertEqual(allocated.tolist(), [25, 75])

    def test_zero_areas_fall_back_to_equal_allocation(self):
        allocated = proportional_allocation(90, pd.Series([0, 0, 0]))
        self.assertEqual(allocated.tolist(), [30, 30, 30])


if __name__ == "__main__":
    unittest.main()
