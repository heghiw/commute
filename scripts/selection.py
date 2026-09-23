from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

try:
    from scripts.portfolio import add_candidate, strategic_destination_nodes, weighted_access_cost
    from scripts.shortlist import build_graph
except ModuleNotFoundError:
    from portfolio import add_candidate, strategic_destination_nodes, weighted_access_cost
    from shortlist import build_graph


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "derived" / "analysis"
REPORT = ROOT / "reports" / "length_utility_optimization_summary.json"
LENGTH_UNIT_M = 10
BUDGETS_M = (250, 500, 1000, 1500, 2500, 5000, 7500, 10000)
WEIGHTS = {"strategic_transport": .50, "underserved_destinations": .30, "jobs_services": .20}


def knapsack(items: pd.DataFrame, budget_m: float) -> list[int]:
    """Maximize additive utility under a whole-link length budget."""
    capacity = int(budget_m // LENGTH_UNIT_M)
    costs = np.ceil(items.length_m.to_numpy() / LENGTH_UNIT_M).astype(int)
    values = items.composite_priority.to_numpy(float)
    dp = np.zeros(capacity + 1)
    keep = np.zeros((len(items), capacity + 1), dtype=bool)
    for i, (cost, value) in enumerate(zip(costs, values)):
        if cost > capacity or value <= 0:
            continue
        previous = dp.copy()
        proposal = previous[:-cost] + value
        improve = proposal > previous[cost:] + 1e-15
        dp[cost:][improve] = proposal[improve]
        keep[i, cost:][improve] = True
    chosen, remaining = [], int(np.argmax(dp))
    for i in range(len(items) - 1, -1, -1):
        if keep[i, remaining]:
            chosen.append(int(items.iloc[i].candidate_id))
            remaining -= costs[i]
    return chosen[::-1]


def objective_costs(graph: nx.DiGraph, origins: pd.DataFrame, destination_sets: dict[str, set[int]]) -> dict[str, float]:
    return {name: weighted_access_cost(nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), nodes, weight="weight"), origins)
            for name, nodes in destination_sets.items()}


def main() -> None:
    items = gpd.read_file(ANALYSIS_DIR / "composite_candidate_ranking.gpkg")
    items = items[items.composite_priority > 0].copy()
    edges = pd.read_csv(ANALYSIS_DIR / "prague_edges_impedance.csv", low_memory=False)
    origins = gpd.read_file(ANALYSIS_DIR / "zsj_accessibility_baseline.gpkg")
    nodes = gpd.read_file(ROOT / "data/derived/graph/prague_graph_nodes.gpkg").to_crs(5514)
    pois = gpd.read_file(ANALYSIS_DIR / "osm_jobs_services_pois.gpkg")
    strategic = strategic_destination_nodes(nodes)
    cutoff = float(origins.strategic_time_min.quantile(.75))
    underserved = set(origins.loc[origins.strategic_time_min >= cutoff, "graph_node"].astype(int))
    services = set(pois.graph_node.astype(int))
    destination_sets = {"strategic_transport": strategic, "underserved_destinations": underserved, "jobs_services": services}

    base_graph = build_graph(edges)
    baseline_cost = objective_costs(base_graph, origins, destination_sets)
    scales = {name: float(items[f"benefit_{name}"].sum()) for name in WEIGHTS}
    rows, selections = [], []
    for budget in BUDGETS_M:
        chosen_ids = knapsack(items, budget)
        chosen = items[items.candidate_id.isin(chosen_ids)].copy()
        graph = build_graph(edges)
        for candidate in chosen.itertuples():
            add_candidate(graph, candidate)
        updated_cost = objective_costs(graph, origins, destination_sets)
        realized = {name: baseline_cost[name] - updated_cost[name] for name in WEIGHTS}
        realized_composite = sum(WEIGHTS[name] * realized[name] / scales[name] for name in WEIGHTS if scales[name] > 0)
        additive_composite = float(chosen.composite_priority.sum())
        used = float(chosen.length_m.sum())
        rows.append({"budget_m": budget, "selected_candidates": len(chosen), "used_length_m": used,
                     "additive_composite_utility": additive_composite,
                     "realized_composite_utility": realized_composite,
                     "realized_utility_per_m": realized_composite / used if used else 0,
                     **{f"realized_{name}": value for name, value in realized.items()}})
        for candidate_id in chosen_ids:
            selections.append({"budget_m": budget, "candidate_id": candidate_id})

    frontier = pd.DataFrame(rows)
    # Keep non-dominated budget outcomes.
    frontier["pareto_efficient"] = frontier.realized_composite_utility > frontier.realized_composite_utility.cummax().shift(fill_value=-1)
    frontier.to_csv(ANALYSIS_DIR / "length_utility_pareto_frontier.csv", index=False)
    pd.DataFrame(selections).to_csv(ANALYSIS_DIR / "length_utility_portfolio_members.csv", index=False)

    efficient = frontier[frontier.pareto_efficient]
    summary = {"method_version": "length-utility-knapsack-v1", "weights": WEIGHTS,
               "length_unit_m": LENGTH_UNIT_M, "candidate_pool": int(len(items)),
               "frontier": efficient.round(6).to_dict("records"),
               "recommended_knee": None,
               "limitations": ["Knapsack selection maximizes additive composite utility before interaction re-evaluation.",
                               "Ten-metre length discretization conservatively rounds candidate lengths upward.",
                               "Restoration length is a physical proxy, not a monetary engineering cost."]}
    if len(efficient) >= 3:
        x = efficient.used_length_m.to_numpy(); y = efficient.realized_composite_utility.to_numpy()
        xn = (x - x.min()) / (x.max() - x.min()); yn = (y - y.min()) / (y.max() - y.min())
        knee_i = int(np.argmax(yn - xn))
        knee = efficient.iloc[knee_i]
        summary["recommended_knee"] = {"budget_m": int(knee.budget_m), "used_length_m": round(float(knee.used_length_m), 2),
                                       "selected_candidates": int(knee.selected_candidates),
                                       "realized_composite_utility": round(float(knee.realized_composite_utility), 6)}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
