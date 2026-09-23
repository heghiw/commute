from __future__ import annotations

import json
import math
import re
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pyogrio
from scipy.spatial import cKDTree

try:
    from scripts.portfolio import strategic_destination_nodes
    from scripts.shortlist import build_graph, counterfactual_cost
except ModuleNotFoundError:
    from portfolio import strategic_destination_nodes
    from shortlist import build_graph, counterfactual_cost


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "derived" / "analysis"
REPORT = ROOT / "reports" / "objectives_summary.json"
SPEED_M_PER_MIN = 250.0
WEIGHT_SCENARIOS = {
    "priority": {"strategic_transport": 0.50, "underserved_destinations": 0.30, "jobs_services": 0.20},
    "transport_heavy": {"strategic_transport": 0.65, "underserved_destinations": 0.25, "jobs_services": 0.10},
    "balanced": {"strategic_transport": 0.40, "underserved_destinations": 0.35, "jobs_services": 0.25},
}
USEFUL_AMENITIES = {"school", "college", "university", "hospital", "clinic", "doctors", "pharmacy", "library",
                    "community_centre", "townhall", "marketplace", "social_facility", "childcare", "arts_centre"}
USEFUL_TOURISM = {"museum", "gallery", "attraction"}
USEFUL_LEISURE = {"sports_centre", "fitness_centre", "stadium"}


def parse_other_tags(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    return dict(re.findall(r'"([^"]+)"=>"([^"]*)"', value))


def useful_poi(tags: dict[str, str]) -> tuple[bool, str | None]:
    if tags.get("amenity") in USEFUL_AMENITIES:
        return True, "amenity"
    if tags.get("office"):
        return True, "office"
    if tags.get("shop"):
        return True, "shop"
    if tags.get("tourism") in USEFUL_TOURISM:
        return True, "tourism"
    if tags.get("leisure") in USEFUL_LEISURE:
        return True, "leisure"
    return False, None


def load_service_pois() -> gpd.GeoDataFrame:
    pbf = ROOT / "data/cleaned/network/praha-latest.osm.pbf"
    points = pyogrio.read_dataframe(pbf, layer="points", columns=["osm_id", "name", "other_tags"])
    parsed = points.other_tags.map(parse_other_tags)
    selected = parsed.map(useful_poi)
    mask = selected.map(lambda x: x[0])
    point_pois = points.loc[mask, ["osm_id", "name", "geometry"]].copy()
    point_pois["poi_type"] = selected[mask].map(lambda x: x[1])

    polygons = pyogrio.read_dataframe(pbf, layer="multipolygons",
                                      columns=["osm_id", "name", "amenity", "office", "shop", "tourism", "leisure"])
    tag_cols = ["amenity", "office", "shop", "tourism", "leisure"]
    keep, types = [], []
    for row in polygons[tag_cols].itertuples(index=False, name=None):
        ok, kind = useful_poi(dict(zip(tag_cols, row)))
        keep.append(ok); types.append(kind)
    polygon_pois = polygons.loc[keep, ["osm_id", "name", "geometry"]].copy()
    polygon_pois["geometry"] = polygon_pois.geometry.representative_point()
    polygon_pois["poi_type"] = np.array(types, dtype=object)[keep]
    return pd.concat([point_pois, polygon_pois], ignore_index=True).set_crs(4326).to_crs(5514)


def main() -> None:
    nodes = gpd.read_file(ROOT / "data/derived/graph/prague_graph_nodes.gpkg").to_crs(5514)
    edges = pd.read_csv(ANALYSIS_DIR / "prague_edges_impedance.csv", low_memory=False)
    origins = gpd.read_file(ANALYSIS_DIR / "zsj_accessibility_baseline.gpkg").to_crs(5514)
    candidates = gpd.read_file(ANALYSIS_DIR / "shortlist_exact_evaluation.gpkg").to_crs(5514)
    graph = build_graph(edges)
    node_ids = nodes.node_id.astype(int).to_numpy()
    node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
    tree = cKDTree(node_xy)

    strategic_nodes = strategic_destination_nodes(nodes)
    priority_cutoff = float(origins.strategic_time_min.quantile(.75))
    underserved = origins[(origins.strategic_time_min >= priority_cutoff) & origins.strategic_time_min.notna()]
    underserved_nodes = set(underserved.graph_node.astype(int))

    pois = load_service_pois()
    poi_xy = np.column_stack([pois.geometry.x, pois.geometry.y])
    pois["snap_distance_m"], idx = tree.query(poi_xy, k=1)
    pois["graph_node"] = node_ids[idx]
    pois = pois[pois.snap_distance_m <= 300].drop_duplicates(["graph_node", "poi_type"])
    service_nodes = set(pois.graph_node.astype(int))
    pois.to_crs(4326).to_file(ANALYSIS_DIR / "osm_jobs_services_pois.gpkg", driver="GPKG")

    destination_sets = {"strategic_transport": strategic_nodes,
                        "underserved_destinations": underserved_nodes,
                        "jobs_services": service_nodes}
    distance_maps = {name: nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), dest, weight="weight")
                     for name, dest in destination_sets.items()}

    aggregates = {int(cid): {"candidate_id": int(cid)} for cid in candidates.candidate_id}
    for cid in aggregates:
        for objective in destination_sets:
            aggregates[cid][f"benefit_{objective}"] = 0.0

    target_nodes = set(candidates.u.astype(int)) | set(candidates.v.astype(int))
    for origin in origins.itertuples():
        bases = {name: distances.get(int(origin.graph_node), math.inf) for name, distances in distance_maps.items()}
        finite = [v for v in bases.values() if math.isfinite(v)]
        if not finite:
            continue
        local = nx.single_source_dijkstra_path_length(graph, int(origin.graph_node), cutoff=max(finite), weight="weight")
        if not target_nodes.intersection(local):
            continue
        for c in candidates.itertuples():
            u, v = int(c.u), int(c.v)
            if u not in local and v not in local:
                continue
            rec = aggregates[int(c.candidate_id)]
            for objective, distances in distance_maps.items():
                base = bases[objective]
                if not math.isfinite(base):
                    continue
                new = counterfactual_cost(base, local.get(u, math.inf), local.get(v, math.inf),
                                          float(c.cost_uv_m), float(c.cost_vu_m),
                                          distances.get(u, math.inf), distances.get(v, math.inf))
                if new < base:
                    rec[f"benefit_{objective}"] += float(origin.population_est) * (base - new) / SPEED_M_PER_MIN

    result = candidates.merge(pd.DataFrame(aggregates.values()), on="candidate_id")
    # Normalize objectives before applying weights.
    for objective in destination_sets:
        col = f"benefit_{objective}"
        scale = result[col].sum()
        result[f"normalized_{objective}"] = result[col] / scale if scale > 0 else 0.0
    for scenario, weights in WEIGHT_SCENARIOS.items():
        result[f"composite_{scenario}"] = sum(weights[o] * result[f"normalized_{o}"] for o in weights)
        result[f"composite_per_m_{scenario}"] = result[f"composite_{scenario}"] / result.length_m
        result[f"rank_{scenario}"] = result[f"composite_per_m_{scenario}"].rank(ascending=False, method="min").astype(int)
    result = result.sort_values("rank_priority")
    result.to_file(ANALYSIS_DIR / "composite_candidate_ranking.gpkg", driver="GPKG")
    result.drop(columns="geometry").to_csv(ANALYSIS_DIR / "composite_candidate_ranking.csv", index=False)

    rank_cols = [f"composite_per_m_{s}" for s in WEIGHT_SCENARIOS]
    corr = result[rank_cols].corr(method="spearman")
    stable = result[result[[f"rank_{s}" for s in WEIGHT_SCENARIOS]].max(axis=1) <= 15]
    summary = {"method_version": "hierarchical-composite-v1", "weights": WEIGHT_SCENARIOS,
               "destinations": {"strategic_transport_nodes": len(strategic_nodes),
                                "underserved_zsj_nodes": len(underserved_nodes),
                                "underserved_threshold_min": round(priority_cutoff, 2),
                                "jobs_services_pois": int(len(pois)), "jobs_services_nodes": len(service_nodes),
                                "poi_types": pois.poi_type.value_counts().to_dict()},
               "positive_candidates": {o: int((result[f"benefit_{o}"] > 0).sum()) for o in destination_sets},
               "rank_spearman": corr.round(3).to_dict(),
               "stable_top15": stable.candidate_id.astype(int).tolist(),
               "top_priority": result.head(10).candidate_id.astype(int).tolist(),
               "limitations": ["OSM POIs indicate opportunities, not employment counts or service capacity.",
                               "General destinations are represented by the least-accessible quartile of ZSJ zones.",
                               "Weights express the user-provided ordering through an initial 0.50/0.30/0.20 split."]}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
