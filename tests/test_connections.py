import unittest

import geopandas as gpd
from shapely.geometry import LineString

from scripts.connections import add_proximity_access, classify_intervention, deduplicate_access, deduplicate_same_road_access, parallel_road_fraction


class ConnectionTests(unittest.TestCase):
    def test_nearby_road_creates_proximity_access(self):
        line = LineString([(0, 0), (100, 0)])
        edges = gpd.GeoDataFrame({"edge_id": [1], "u": [10], "v": [11],
                                  "cycle_existing": [False], "osm_highway": ["residential"]},
                                 geometry=[LineString([(50, 10), (50, 30)])], crs=5514)
        records = []
        add_proximity_access(records, line, edges, edges.sindex)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "proximity")
        self.assertAlmostEqual(records[0]["connector_m"], 10)

    def test_coincident_access_points_merge(self):
        rows = [{"measure_m": 10, "connector_m": 2, "cycle_existing": False},
                {"measure_m": 10.5, "connector_m": 0, "cycle_existing": True}]
        merged = deduplicate_access(rows)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["cycle_existing"])

    def test_proximity_to_same_road_as_crossing_is_removed(self):
        rows = [
            {"measure_m": 354, "connector_m": 15, "edge_id": 10, "edge_u": 1, "edge_v": 2,
             "osm_highway": "service", "cycle_existing": True, "kind": "proximity"},
            {"measure_m": 358, "connector_m": 0, "edge_id": 11, "edge_u": 2, "edge_v": 3,
             "osm_highway": "service", "cycle_existing": True, "kind": "interior"},
        ]
        self.assertEqual([r["kind"] for r in deduplicate_same_road_access(rows)], ["interior"])

    def test_distinct_road_crossings_are_preserved(self):
        rows = [
            {"measure_m": 50, "connector_m": 0, "edge_id": 10, "edge_u": 1, "edge_v": 2,
             "osm_highway": "service", "cycle_existing": True, "kind": "interior"},
            {"measure_m": 51, "connector_m": 0, "edge_id": 12, "edge_u": 2, "edge_v": 4,
             "osm_highway": "tertiary", "cycle_existing": False, "kind": "interior"},
        ]
        self.assertEqual(len(deduplicate_same_road_access(rows)), 2)

    def test_parallel_road_is_detected_but_perpendicular_road_is_not(self):
        line = LineString([(0, 0), (100, 0)])
        parallel = gpd.GeoDataFrame(geometry=[LineString([(0, 20), (100, 20)])], crs=5514)
        perpendicular = gpd.GeoDataFrame(geometry=[LineString([(50, -20), (50, 20)])], crs=5514)
        self.assertGreater(parallel_road_fraction(line, parallel, parallel.sindex), .9)
        self.assertEqual(parallel_road_fraction(line, perpendicular, perpendicular.sindex), 0)

    def test_tertiary_crossing_is_tier_b_not_rejected(self):
        records = [{"active": True, "unsafe_major_crossing": True, "osm_highway": "tertiary"}]
        tier, _, count = classify_intervention(records, True)
        self.assertEqual((tier, count), ("B", 1))

    def test_primary_crossing_is_tier_c(self):
        records = [{"active": True, "unsafe_major_crossing": True, "osm_highway": "primary"}]
        tier, _, count = classify_intervention(records, True)
        self.assertEqual((tier, count), ("C", 1))


if __name__ == "__main__":
    unittest.main()
