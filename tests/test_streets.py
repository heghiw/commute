import unittest

from scripts.streets import cycling_cost


class CorrectedGraphTests(unittest.TestCase):
    def test_current_cycle_edge_is_cheaper(self):
        self.assertLess(cycling_cost(100, 0, True), cycling_cost(100, 0, False))

    def test_uphill_is_directional(self):
        self.assertGreater(cycling_cost(100, .05, False), cycling_cost(100, -.05, False))


if __name__ == "__main__":
    unittest.main()
