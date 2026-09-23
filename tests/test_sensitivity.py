import unittest

import pandas as pd

from scripts.sensitivity import rank_overlap


class SensitivityTests(unittest.TestCase):
    def test_rank_overlap(self):
        a = pd.Series([10, 9, 1], index=[1, 2, 3])
        b = pd.Series([8, 1, 7], index=[1, 2, 3])
        self.assertEqual(rank_overlap(a, b, 2), 1)


if __name__ == "__main__":
    unittest.main()
