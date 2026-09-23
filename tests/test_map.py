import unittest

import pandas as pd

from scripts.map import status


class MapStatusTests(unittest.TestCase):
    def test_parallel_option_is_visible_even_without_network_access(self):
        row = pd.Series({"routable_corridor": False, "parallel_existing_road_fraction": .75,
                         "material_candidate": False, "planning_candidate": False,
                         "requires_crossing_review": False, "composite_utility": 0})
        self.assertEqual(status(row), "parallel")

    def test_unconnected_is_separate_from_low_value_connected(self):
        row = pd.Series({"routable_corridor": False, "parallel_existing_road_fraction": .1,
                         "material_candidate": False, "planning_candidate": False,
                         "requires_crossing_review": False, "composite_utility": 0})
        self.assertEqual(status(row), "unconnected")
        row.routable_corridor = True
        self.assertEqual(status(row), "other")

    def test_zero_benefit_is_not_a_screened_link(self):
        row = pd.Series({"routable_corridor": True, "parallel_existing_road_fraction": .1,
                         "material_candidate": True, "planning_candidate": True,
                         "requires_crossing_review": False, "composite_utility": 0})
        self.assertEqual(status(row), "other")


if __name__ == "__main__":
    unittest.main()
