import unittest

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Point

from scripts.stations import snap_nodes


class TransitDestinationTests(unittest.TestCase):
    def test_snap_limit_and_duplicate_nodes(self):
        points = gpd.GeoSeries([Point(3, 0), Point(4, 0), Point(210, 0)], crs=5514)
        tree = cKDTree(np.array([[0.0, 0.0], [100.0, 0.0]]))
        nodes, count = snap_nodes(points, tree, np.array([10, 20]), max_distance_m=20)
        self.assertEqual(nodes, {10})
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
