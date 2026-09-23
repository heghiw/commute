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
    from scripts.corridors import build_graph, nearest
    from scripts.stations import load_destinations
except ModuleNotFoundError:
    from corridors import build_graph, nearest
    from stations import load_destinations


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data/derived/corrected_graph"
OUT = ROOT / "data/derived/corrected_analysis"
REPORT = ROOT / "reports/edge_connected_scoring_summary.json"
CRS = 5514
SPEED = 250.0
WEIGHTS = {"metro": .25, "train": .25, "underserved_destinations": .30, "jobs_services": .20}
FEASIBILITY_FACTOR = {"A": 1.0, "B": 0.65, "C": 0.30}
MAX_EXISTING_ROAD_OVERLAP = 0.60
MAX_PARALLEL_ROAD_FRACTION = 0.60


def ready_candidate_mask(frame: pd.DataFrame) -> pd.Series:
    return frame.planning_candidate & ~frame.requires_crossing_review


def main() -> None:
    nodes = gpd.read_file(GRAPH / "nodes.gpkg").to_crs(CRS)
    edges = pd.read_csv(GRAPH / "edges.csv", low_memory=False).set_index("edge_id")
    graph = build_graph(edges.reset_index())
    node_ids = nodes.node_id.astype(int).to_numpy(); node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y]); tree = cKDTree(node_xy)
    corridors = gpd.read_file(OUT / "edge_connected_corridors.gpkg").to_crs(CRS)
    corridors = corridors[corridors.routable_corridor].copy()
    origins = gpd.read_file(OUT / "corrected_zsj_accessibility.gpkg").to_crs(CRS)

    transit_nodes, _ = load_destinations(tree, node_ids)
    strategic = transit_nodes["metro"] | transit_nodes["train"]
    strategic_dist = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), strategic, weight="weight")
    origins["strategic_time_min"] = origins.graph_node.map(strategic_dist) / SPEED
    cutoff = origins.strategic_time_min.quantile(.75)
    underserved = set(origins.loc[origins.strategic_time_min >= cutoff, "graph_node"].astype(int))
    pois = gpd.read_file(ROOT / "data/derived/analysis/osm_jobs_services_pois.gpkg").to_crs(CRS)
    pid, pdist = nearest(tree, node_ids, np.column_stack([pois.geometry.x, pois.geometry.y])); services = set(pid[pdist <= 300].astype(int))
    destinations = {**transit_nodes, "underserved_destinations": underserved, "jobs_services": services}
    distance_maps = {k: nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), v, weight="weight") for k, v in destinations.items()}

    access = {int(r.corridor_id): [x for x in json.loads(r.access_json) if x["active"]] for r in corridors.itertuples()}
    target_nodes = {int(x[key]) for records in access.values() for x in records for key in ("edge_u", "edge_v")}
    benefits = {cid: {f"benefit_{name}": 0.0 for name in destinations} for cid in access}

    for origin in origins.itertuples():
        bases = {name: d.get(int(origin.graph_node), math.inf) for name, d in distance_maps.items()}
        finite = [v for v in bases.values() if math.isfinite(v)]
        if not finite: continue
        local = nx.single_source_dijkstra_path_length(graph, int(origin.graph_node), cutoff=max(finite), weight="weight")
        if not target_nodes.intersection(local): continue
        for cid, records in access.items():
            origin_access = []
            for a in records:
                edge = edges.loc[int(a["edge_id"])]; f = float(a["edge_fraction"]); connector = float(a["connector_m"])
                cost = min(local.get(int(a["edge_u"]), math.inf) + edge.cost_uv_m * f,
                           local.get(int(a["edge_v"]), math.inf) + edge.cost_vu_m * (1-f)) + connector
                origin_access.append(cost)
            if not any(math.isfinite(x) for x in origin_access): continue
            for name, dmap in distance_maps.items():
                base = bases[name]; best = base
                for i, a in enumerate(records):
                    if not math.isfinite(origin_access[i]): continue
                    for j, b in enumerate(records):
                        if i == j: continue
                        edge = edges.loc[int(b["edge_id"])]; f = float(b["edge_fraction"]); connector = float(b["connector_m"])
                        exit_cost = min(edge.cost_vu_m * f + dmap.get(int(b["edge_u"]), math.inf),
                                        edge.cost_uv_m * (1-f) + dmap.get(int(b["edge_v"]), math.inf)) + connector
                        corridor_cost = abs(float(a["measure_m"]) - float(b["measure_m"]))
                        best = min(best, origin_access[i] + corridor_cost + exit_cost)
                if best < base:
                    benefits[cid][f"benefit_{name}"] += float(origin.population_est) * (base-best) / SPEED

    benefit_df = pd.DataFrame.from_dict(benefits, orient="index").rename_axis("corridor_id").reset_index()
    result = corridors.drop(columns=[c for c in corridors.columns if c.startswith("benefit_") or c.startswith("normalized_") or c in ["composite_utility", "utility_per_m", "rank"]], errors="ignore").merge(benefit_df, on="corridor_id")
    for name in destinations:
        total = result[f"benefit_{name}"].sum(); result[f"normalized_{name}"] = result[f"benefit_{name}"] / total if total else 0
    result["benefit_strategic_transport"] = result.benefit_metro + result.benefit_train
    result["normalized_strategic_transport"] = (result.normalized_metro + result.normalized_train) / 2
    result["composite_utility"] = sum(WEIGHTS[n]*result[f"normalized_{n}"] for n in WEIGHTS)
    result["utility_per_m"] = (result.composite_utility / result.intervention_length_m).replace([np.inf, -np.inf], np.nan).fillna(0)
    distance_cache = {}
    def node_distance(u, v):
        key = (int(u), int(v))
        if key not in distance_cache:
            try: distance_cache[key] = nx.shortest_path_length(graph, key[0], key[1], weight="weight")
            except nx.NetworkXNoPath: distance_cache[key] = math.inf
        return distance_cache[key]
    detours = []
    for row in result.itertuples():
        records = access[int(row.corridor_id)]; best_ratio, best_saved = 0.0, 0.0
        for i, a in enumerate(records):
            ea = edges.loc[int(a["edge_id"])]; fa = float(a["edge_fraction"]); ca = float(a["connector_m"])
            starts = [(a["edge_u"], ca + ea.cost_vu_m*fa), (a["edge_v"], ca + ea.cost_uv_m*(1-fa))]
            for b in records[i+1:]:
                eb = edges.loc[int(b["edge_id"])]; fb = float(b["edge_fraction"]); cb = float(b["connector_m"])
                ends = [(b["edge_u"], cb + eb.cost_uv_m*fb), (b["edge_v"], cb + eb.cost_vu_m*(1-fb))]
                existing = min(sa + node_distance(u,v) + sb for u,sa in starts for v,sb in ends)
                restored = abs(float(a["measure_m"])-float(b["measure_m"])) + ca + cb
                if math.isfinite(existing) and restored > 0:
                    best_ratio = max(best_ratio, existing/restored); best_saved = max(best_saved, existing-restored)
        detours.append((best_ratio,best_saved))
    result["max_detour_ratio"] = [x[0] for x in detours]
    result["max_detour_saved_m"] = [x[1] for x in detours]
    result["planning_candidate"] = ((result.max_detour_ratio >= 1.10)
                                    & (result.max_detour_saved_m >= 100)
                                    & (result.near_existing_road_fraction <= MAX_EXISTING_ROAD_OVERLAP)
                                    & (result.parallel_existing_road_fraction <= MAX_PARALLEL_ROAD_FRACTION))
    # Keep crossing-dependent benefit separate from the ready shortlist.
    result["requires_crossing_review"] = result.unsafe_major_crossing.astype(bool)
    result["material_candidate"] = ready_candidate_mask(result)
    result["feasibility_factor"] = result.intervention_tier.map(FEASIBILITY_FACTOR).fillna(0.3)
    result["decision_utility_per_m"] = (result.utility_per_m * result.feasibility_factor).where(result.material_candidate, 0)
    result["rank"] = result.decision_utility_per_m.rank(ascending=False, method="min").astype(int)
    result = result.sort_values("rank")
    result.to_file(OUT / "edge_connected_corridor_ranking.gpkg", driver="GPKG")
    result.drop(columns="geometry").to_csv(OUT / "edge_connected_corridor_ranking.csv", index=False)
    positive = result[(result.composite_utility > 0) & result.material_candidate]
    unresolved = result[(result.composite_utility > 0) & result.planning_candidate & result.requires_crossing_review]
    summary = {"method": "metro-train-edge-access-counterfactual-v4", "routable_corridors": len(result), "positive_corridors": len(positive),
               "objective_weights": WEIGHTS, "transit_destination_nodes": {k: len(v) for k, v in transit_nodes.items()},
               "materiality": {"minimum_detour_ratio": 1.10, "minimum_saved_m": 100},
               "maximum_existing_road_overlap": MAX_EXISTING_ROAD_OVERLAP,
               "maximum_parallel_road_fraction": MAX_PARALLEL_ROAD_FRACTION,
               "unresolved_crossing_opportunities": int(len(unresolved)),
               "unresolved_crossing_ids": unresolved.corridor_id.astype(int).tolist(),
               "feasibility_factors": FEASIBILITY_FACTOR,
               "positive_by_tier": {k: int(v) for k, v in positive.intervention_tier.value_counts().to_dict().items()},
               "positive_direct_cycle_closures": int(((positive.classification == "direct_cycle_closure")).sum()),
               "positive_cycle_extensions": int(((positive.classification == "cycle_extension")).sum()),
               "positive_street_links": int(((positive.classification == "street_network_link")).sum()),
               "top10": positive.head(10).corridor_id.astype(int).tolist()}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__": main()
