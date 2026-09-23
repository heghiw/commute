import unittest

from scripts.demand import cycling_cost


class CyclingCostTests(unittest.TestCase):
    def test_flat_street_equals_length(self):
        self.assertEqual(cycling_cost(100, 0, 1), 100)

    def test_cycle_route_reduces_flat_cost(self):
        self.assertEqual(cycling_cost(100, 0, .75), 75)

    def test_uphill_costs_more_than_downhill(self):
        self.assertGreater(cycling_cost(100, .05), cycling_cost(100, -.05))

    def test_cost_is_directional(self):
        self.assertNotEqual(cycling_cost(100, .03), cycling_cost(100, -.03))


if __name__ == "__main__":
    unittest.main()
