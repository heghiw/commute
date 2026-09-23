from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

try:
    from scripts.shortlist import build_graph
except ModuleNotFoundError:  # Direct execution: python scripts/portfolio.py
    from shortlist import build_graph


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "derived" / "analysis"
REPORT = ROOT / "reports" / "portfolio_evaluation_summary.json"
SPEED_M_PER_MIN = 250.0
BUDGETS_M = (1000, 2500, 5000, 10000)


def weighted_access_cost(distances: dict[int, float], origins: pd.DataFrame) -> float:
    values = origins.graph_node.map(distances)
    reachable = values.notna()
    return float((values[reachable] / SPEED_M_PER_MIN * origins.loc[reachable, "population_est"]).sum())


def strategic_destination_nodes(nodes: gpd.GeoDataFrame) -> set[int]:
    stops = pd.read_csv(ROOT / "data/cleaned/gtfs/stops.csv", low_memory=False)
    route_stops = pd.read_csv(ROOT / "data/cleaned/gtfs/route_stops.csv", low_memory=False)
    routes = pd.read_csv(ROOT / "data/cleaned/gtfs/routes.csv", low_memory=False)
    served = route_stops[["route_id", "stop_id"]].drop_duplicates().merge(routes[["route_id", "route_type"]], on="route_id")
    service = served.groupby("stop_id").agg(route_count=("route_id", "nunique"), mode_count=("route_type", "nunique"),
                                             rail_metro=("route_type", lambda x: x.astype(str).isin(["1", "2"]).any())).reset_index()
    stops = stops.merge(service, on="stop_id", how="inner")
    stops = stops[(stops.rail_metro) | (stops.route_count >= 5) | (stops.mode_count >= 3)].dropna(subset=["stop_lon", "stop_lat"])
    points = gpd.GeoSeries(gpd.points_from_xy(stops.stop_lon, stops.stop_lat), crs=4326).to_crs(5514)
    node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
    distance, index = cKDTree(node_xy).query(np.column_stack([points.x, points.y]), k=1)
    return set(nodes.node_id.astype(int).to_numpy()[index][distance <= 500])


def add_candidate(graph: nx.DiGraph, row) -> list[tuple[int, int, dict | None]]:
    changed = []
    for a, b, cost in ((int(row.u), int(row.v), float(row.cost_uv_m)),
                       (int(row.v), int(row.u), float(row.cost_vu_m))):
        old = graph.get_edge_data(a, b)
        if old is None or cost < old["weight"]:
            changed.append((a, b, None if old is None else dict(old)))
            graph.add_edge(a, b, weight=cost)
    return changed


def revert_candidate(graph: nx.DiGraph, changed: list[tuple[int, int, dict | None]]) -> None:
    for a, b, old in changed:
        if old is None:
            graph.remove_edge(a, b)
        else:
            graph[a][b].clear()
            graph[a][b].update(old)


def main() -> None:
    edges = pd.read_csv(ANALYSIS_DIR / "prague_edges_impedance.csv", low_memory=False)
    origins = gpd.read_file(ANALYSIS_DIR / "zsj_accessibility_baseline.gpkg")
    nodes = gpd.read_file(ROOT / "data/derived/graph/prague_graph_nodes.gpkg").to_crs(5514)
    candidates = gpd.read_file(ANALYSIS_DIR / "shortlist_exact_evaluation.gpkg")
    candidates = candidates[candidates.exact_population_minutes_saved > 0].sort_values("exact_benefit_per_m", ascending=False)
    destinations = strategic_destination_nodes(nodes)

    selections, summaries = [], []
    for budget in BUDGETS_M:
        graph = build_graph(edges)
        distances = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), destinations, weight="weight")
        current_cost = weighted_access_cost(distances, origins)
        baseline_cost = current_cost
        used = 0.0
        accepted = 0
        for row in candidates.itertuples():
            if used + row.length_m > budget:
                continue
            changed = add_candidate(graph, row)
            if not changed:
                continue
            new_distances = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), destinations, weight="weight")
            new_cost = weighted_access_cost(new_distances, origins)
            marginal = current_cost - new_cost
            if marginal <= 1e-6:
                revert_candidate(graph, changed)
                continue
            used += float(row.length_m)
            accepted += 1
            current_cost = new_cost
            selections.append({"budget_m": budget, "selection_order": accepted, "candidate_id": int(row.candidate_id),
                               "length_m": float(row.length_m), "cumulative_length_m": used,
                               "marginal_population_minutes_saved": marginal,
                               "cumulative_population_minutes_saved": baseline_cost - current_cost})
        summaries.append({"budget_m": budget, "selected_candidates": accepted, "used_length_m": used,
                          "interaction_adjusted_population_minutes_saved": baseline_cost - current_cost})

    selections_df = pd.DataFrame(selections)
    summaries_df = pd.DataFrame(summaries)
    selections_df.to_csv(ANALYSIS_DIR / "interaction_adjusted_portfolio_selections.csv", index=False)
    summaries_df.to_csv(ANALYSIS_DIR / "interaction_adjusted_budget_scenarios.csv", index=False)
    additive = pd.read_csv(ANALYSIS_DIR / "preliminary_budget_scenarios.csv")
    comparison = summaries_df.merge(additive, on="budget_m", suffixes=("_interaction", "_standalone"))
    comparison["overlap_reduction_pct"] = 100 * (1 - comparison.interaction_adjusted_population_minutes_saved /
                                                   comparison.standalone_population_minutes_saved)
    summary = {"method_version": "interaction-adjusted-greedy-v4",
               "budgets": summaries,
               "comparison": comparison[["budget_m", "standalone_population_minutes_saved",
                                          "interaction_adjusted_population_minutes_saved", "overlap_reduction_pct"]].round(3).to_dict("records"),
               "method_note": "Candidates are considered in standalone benefit-per-metre order; each accepted marginal is recomputed on the updated graph.",
               "limitation": "The heuristic does not search all remaining candidates by recalculated marginal benefit at every iteration."}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
