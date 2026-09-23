import unittest

import geopandas as gpd
from shapely.geometry import LineString, Point

from scripts.transit import nearest_distances


class TransitDistanceTests(unittest.TestCase):
    def test_nearest_geometry_distance(self):
        corridors = gpd.GeoSeries([LineString([(0, 0), (10, 0)])], crs=5514)
        entrances = gpd.GeoSeries([Point(20, 0), Point(5, 5)], crs=5514)
        self.assertAlmostEqual(nearest_distances(corridors, entrances)[0], 5)


if __name__ == "__main__":
    unittest.main()
