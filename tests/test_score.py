import unittest
import pandas as pd
from scripts.score import ready_candidate_mask


class CrossingGateTests(unittest.TestCase):
    def test_unresolved_crossing_is_not_ready(self):
        frame = pd.DataFrame({"planning_candidate": [True, True, False],
                              "requires_crossing_review": [True, False, False]})
        self.assertEqual(ready_candidate_mask(frame).tolist(), [False, True, False])


if __name__ == "__main__":
    unittest.main()
