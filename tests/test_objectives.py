import unittest

from scripts.objectives import parse_other_tags, useful_poi


class CompositeObjectiveTests(unittest.TestCase):
    def test_osm_tag_parser(self):
        self.assertEqual(parse_other_tags('"amenity"=>"school","wheelchair"=>"yes"')["amenity"], "school")

    def test_school_is_useful(self):
        self.assertEqual(useful_poi({"amenity": "school"}), (True, "amenity"))

    def test_parking_is_not_selected(self):
        self.assertEqual(useful_poi({"amenity": "parking"}), (False, None))


if __name__ == "__main__":
    unittest.main()
