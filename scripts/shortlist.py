from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "derived" / "analysis"
REPORT = ROOT / "reports" / "shortlist_evaluation_summary.json"
SPEED_M_PER_MIN = 250.0
SHORTLIST_SIZE = 100
BUDGETS_M = (1000, 2500, 5000, 10000)


def counterfactual_cost(base: float, to_u: float, to_v: float, uv: float, vu: float,
                        u_to_dest: float, v_to_dest: float) -> float:
    return min(base, to_u + uv + v_to_dest, to_v + vu + u_to_dest)


def greedy_select(items: pd.DataFrame, budget_m: float) -> pd.DataFrame:
    """Select whole candidates by exact standalone benefit per metre."""
    selected, used = [], 0.0
    for row in items.sort_values("exact_benefit_per_m", ascending=False).itertuples():
        if row.exact_population_minutes_saved <= 0:
            continue
        if used + row.length_m <= budget_m:
            selected.append(row.candidate_id)
            used += row.length_m
    return items[items.candidate_id.isin(selected)].copy()


def build_graph(edges: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    for row in edges[["u", "v", "cost_uv_m", "cost_vu_m"]].itertuples(index=False):
        for a, b, cost in ((int(row.u), int(row.v), row.cost_uv_m), (int(row.v), int(row.u), row.cost_vu_m)):
            if cost < graph.get_edge_data(a, b, {}).get("weight", math.inf):
                graph.add_edge(a, b, weight=float(cost))
    return graph


def main() -> None:
    edges = pd.read_csv(ANALYSIS_DIR / "prague_edges_impedance.csv", low_memory=False)
    origins = gpd.read_file(ANALYSIS_DIR / "zsj_accessibility_baseline.gpkg")
    candidates = gpd.read_file(ANALYSIS_DIR / "vanished_paths_refined_screening.gpkg")
    shortlist = candidates[candidates.snap_ok & (candidates.screening_benefit_per_m > 0)].nlargest(
        SHORTLIST_SIZE, "screening_benefit_per_m"
    ).copy()
    graph = build_graph(edges)

    # Rebuild destination nodes using the same GTFS selection rule.
    destination_distance = dict(zip(origins.graph_node.astype(int), origins.strategic_cost_m))
    stops = pd.read_csv(ROOT / "data/cleaned/gtfs/stops.csv", low_memory=False)
    route_stops = pd.read_csv(ROOT / "data/cleaned/gtfs/route_stops.csv", low_memory=False)
    routes = pd.read_csv(ROOT / "data/cleaned/gtfs/routes.csv", low_memory=False)
    served = route_stops[["route_id", "stop_id"]].drop_duplicates().merge(routes[["route_id", "route_type"]], on="route_id")
    service = served.groupby("stop_id").agg(route_count=("route_id", "nunique"), mode_count=("route_type", "nunique"),
                                             rail_metro=("route_type", lambda x: x.astype(str).isin(["1", "2"]).any())).reset_index()
    stops = stops.merge(service, on="stop_id", how="inner")
    stops = stops[(stops.rail_metro) | (stops.route_count >= 5) | (stops.mode_count >= 3)].dropna(subset=["stop_lon", "stop_lat"])
    nodes = gpd.read_file(ROOT / "data/derived/graph/prague_graph_nodes.gpkg").to_crs(5514)
    stop_points = gpd.GeoSeries(gpd.points_from_xy(stops.stop_lon, stops.stop_lat), crs=4326).to_crs(5514)
    from scipy.spatial import cKDTree
    node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
    distance, index = cKDTree(node_xy).query(np.column_stack([stop_points.x, stop_points.y]), k=1)
    destination_nodes = set(nodes.node_id.astype(int).to_numpy()[index][distance <= 500])
    to_destination = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), destination_nodes, weight="weight")

    records = {int(cid): {"candidate_id": int(cid), "exact_population_minutes_saved": 0.0,
                          "exact_improved_population": 0.0, "origins_improved": 0}
               for cid in shortlist.candidate_id}
    origin_candidate_rows = []
    target_nodes = set(shortlist.u.astype(int)) | set(shortlist.v.astype(int))

    for origin in origins.itertuples():
        base = float(origin.strategic_cost_m) if pd.notna(origin.strategic_cost_m) else math.inf
        if not math.isfinite(base):
            continue
        # Any endpoint farther than the existing destination cannot produce an improvement.
        local = nx.single_source_dijkstra_path_length(graph, int(origin.graph_node), cutoff=base, weight="weight")
        relevant = target_nodes.intersection(local)
        if not relevant:
            continue
        for c in shortlist.itertuples():
            u, v = int(c.u), int(c.v)
            new = counterfactual_cost(base, local.get(u, math.inf), local.get(v, math.inf),
                                      float(c.cost_uv_m), float(c.cost_vu_m),
                                      to_destination.get(u, math.inf), to_destination.get(v, math.inf))
            if new < base - 1e-6:
                saved = (base - new) / SPEED_M_PER_MIN
                rec = records[int(c.candidate_id)]
                rec["exact_population_minutes_saved"] += float(origin.population_est) * saved
                rec["exact_improved_population"] += float(origin.population_est)
                rec["origins_improved"] += 1
                origin_candidate_rows.append({"candidate_id": int(c.candidate_id), "zsj_code": origin.kod,
                                              "city_part_name": origin.city_part_name,
                                              "minutes_saved": saved, "population_area_weighted": float(origin.population_est)})

    exact = pd.DataFrame(records.values())
    shortlist = shortlist.merge(exact, on="candidate_id")
    shortlist["exact_benefit_per_m"] = shortlist.exact_population_minutes_saved / shortlist.length_m
    shortlist["screening_rank"] = shortlist.screening_benefit_per_m.rank(ascending=False, method="min").astype(int)
    shortlist["exact_rank"] = shortlist.exact_benefit_per_m.rank(ascending=False, method="min").astype(int)
    shortlist["rank_shift"] = shortlist.screening_rank - shortlist.exact_rank
    shortlist = shortlist.sort_values("exact_rank")
    shortlist.to_file(ANALYSIS_DIR / "shortlist_exact_evaluation.gpkg", driver="GPKG")
    shortlist.drop(columns="geometry").to_csv(ANALYSIS_DIR / "shortlist_exact_evaluation.csv", index=False)
    pd.DataFrame(origin_candidate_rows).to_csv(ANALYSIS_DIR / "shortlist_origin_benefits.csv", index=False)

    portfolio_rows = []
    for budget in BUDGETS_M:
        chosen = greedy_select(shortlist, budget)
        portfolio_rows.append({"budget_m": budget, "selected_candidates": len(chosen),
                               "used_length_m": float(chosen.length_m.sum()),
                               "standalone_population_minutes_saved": float(chosen.exact_population_minutes_saved.sum())})
    portfolios = pd.DataFrame(portfolio_rows)
    portfolios.to_csv(ANALYSIS_DIR / "preliminary_budget_scenarios.csv", index=False)

    positive = shortlist[shortlist.exact_population_minutes_saved > 0]
    correlation = shortlist[["screening_benefit_per_m", "exact_benefit_per_m"]].corr(method="spearman").iloc[0, 1]
    summary = {
        "method_version": "exact-shortlist-v3",
        "shortlist": {"screened_candidates": int(len(shortlist)), "positive_exact_benefit": int(len(positive)),
                      "origins": int(len(origins)), "spearman_screening_vs_exact": round(float(correlation), 3)},
        "best_candidate": None if positive.empty else {
            "candidate_id": int(positive.iloc[0].candidate_id),
            "length_m": round(float(positive.iloc[0].length_m), 2),
            "population_minutes_saved": round(float(positive.iloc[0].exact_population_minutes_saved), 2),
            "benefit_per_m": round(float(positive.iloc[0].exact_benefit_per_m), 2)},
        "budgets": portfolio_rows,
        "warning": "Budget scenarios sum standalone effects and do not model interaction or overlapping beneficiaries.",
    }
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
