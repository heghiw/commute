import unittest

import networkx as nx
import pandas as pd

from scripts.portfolio import add_candidate, revert_candidate, weighted_access_cost


class PortfolioTests(unittest.TestCase):
    def test_weighted_access_cost(self):
        origins = pd.DataFrame({"graph_node": [1, 2], "population_est": [100, 50]})
        self.assertEqual(weighted_access_cost({1: 250, 2: 500}, origins), 200)

    def test_candidate_add_and_revert(self):
        graph = nx.DiGraph()
        row = type("Candidate", (), {"u": 1, "v": 2, "cost_uv_m": 10, "cost_vu_m": 20})()
        changed = add_candidate(graph, row)
        self.assertEqual(graph[1][2]["weight"], 10)
        revert_candidate(graph, changed)
        self.assertFalse(graph.has_edge(1, 2))


if __name__ == "__main__":
    unittest.main()
