from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely import get_parts, line_merge
from shapely.geometry import LineString, MultiLineString

try:
    from scripts.stations import load_destinations
except ModuleNotFoundError:
    from stations import load_destinations


ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data/derived/corrected_graph"
OUT = ROOT / "data/derived/corrected_analysis"
REPORT = ROOT / "reports/corrected_analysis_summary.json"
CRS = 5514
SNAP_LIMIT_M = 20.0
SPEED_M_PER_MIN = 250.0
WEIGHTS = {"strategic_transport": .50, "underserved_destinations": .30, "jobs_services": .20}


def build_graph(edges: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    for r in edges.itertuples():
        for a, b, cost in ((int(r.u), int(r.v), r.cost_uv_m), (int(r.v), int(r.u), r.cost_vu_m)):
            if cost < graph.get_edge_data(a, b, {}).get("weight", math.inf):
                graph.add_edge(a, b, weight=float(cost))
    return graph


def nearest(tree, node_ids, xy):
    distance, index = tree.query(xy, k=1)
    return node_ids[index], distance


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nodes = gpd.read_file(GRAPH / "nodes.gpkg").to_crs(CRS)
    edges = pd.read_csv(GRAPH / "edges.csv", low_memory=False)
    graph = build_graph(edges)
    node_ids = nodes.node_id.astype(int).to_numpy()
    node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
    tree = cKDTree(node_xy)
    elevation = dict(zip(node_ids, nodes.elevation_m))

    vanished = gpd.read_file(ROOT / "data/cleaned/historical_paths/praha_vanished_paths.gpkg").to_crs(CRS)
    # Merge connected historical fragments.
    merged = line_merge(vanished.geometry.union_all())
    corridor_lines = [part for part in get_parts(merged) if part.geom_type == "LineString" and part.length > 1]
    corridors = gpd.GeoDataFrame({"corridor_id": np.arange(1, len(corridor_lines) + 1), "geometry": corridor_lines}, crs=CRS)
    corridors["historical_length_m"] = corridors.length
    start = np.array([g.coords[0] for g in corridors.geometry]); end = np.array([g.coords[-1] for g in corridors.geometry])
    corridors["u"], corridors["snap_u_m"] = nearest(tree, node_ids, start)
    corridors["v"], corridors["snap_v_m"] = nearest(tree, node_ids, end)
    node_point = dict(zip(node_ids, nodes.geometry))
    corridors["connector_u"] = [LineString([tuple(a), node_point[int(u)]]) for a, u in zip(start, corridors.u)]
    corridors["connector_v"] = [LineString([tuple(b), node_point[int(v)]]) for b, v in zip(end, corridors.v)]
    corridors["connector_length_m"] = corridors.snap_u_m + corridors.snap_v_m
    corridors["intervention_length_m"] = corridors.historical_length_m + corridors.connector_length_m
    corridors["snap_valid"] = (corridors.snap_u_m <= SNAP_LIMIT_M) & (corridors.snap_v_m <= SNAP_LIMIT_M) & (corridors.u != corridors.v)
    corridors["display_geometry"] = [MultiLineString([cu, line, cv]) for cu, line, cv in zip(corridors.connector_u, corridors.geometry, corridors.connector_v)]
    corridors["historical_wkt"] = corridors.geometry.to_wkt()
    corridors["connector_u_wkt"] = gpd.GeoSeries(corridors.connector_u, crs=CRS).to_wkt().to_numpy()
    corridors["connector_v_wkt"] = gpd.GeoSeries(corridors.connector_v, crs=CRS).to_wkt().to_numpy()
    z_u = corridors.u.map(elevation); z_v = corridors.v.map(elevation)
    grade = ((z_v - z_u) / corridors.intervention_length_m).clip(-.25, .25).fillna(0)
    corridors["cost_uv_m"] = corridors.intervention_length_m * (1 + 8 * grade.clip(lower=0) + 1.5 * (-grade).clip(lower=0))
    corridors["cost_vu_m"] = corridors.intervention_length_m * (1 + 8 * (-grade).clip(lower=0) + 1.5 * grade.clip(lower=0))

    # Snap demand origins to the street graph.
    origins = gpd.read_file(ROOT / "data/derived/analysis/zsj_accessibility_baseline.gpkg").to_crs(CRS)
    oxy = np.column_stack([origins.geometry.representative_point().x, origins.geometry.representative_point().y])
    origins["graph_node"], origins["snap_distance_m"] = nearest(tree, node_ids, oxy)

    transit_nodes, transit_summary = load_destinations(tree, node_ids)
    (ROOT / "reports/transit_destination_summary.json").write_text(
        json.dumps(transit_summary, indent=2), encoding="utf-8"
    )
    strategic_nodes = transit_nodes["metro"] | transit_nodes["train"]
    strategic_dist = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), strategic_nodes, weight="weight")
    origins["strategic_time_min"] = origins.graph_node.map(strategic_dist) / SPEED_M_PER_MIN
    inaccessible_cutoff = float(origins.strategic_time_min.quantile(.75))
    underserved_nodes = set(origins.loc[origins.strategic_time_min >= inaccessible_cutoff, "graph_node"].astype(int))
    pois = gpd.read_file(ROOT / "data/derived/analysis/osm_jobs_services_pois.gpkg").to_crs(CRS)
    pxy = np.column_stack([pois.geometry.x, pois.geometry.y]); pid, pdist = nearest(tree, node_ids, pxy)
    service_nodes = set(pid[pdist <= 300].astype(int))
    destination_sets = {"strategic_transport": strategic_nodes, "underserved_destinations": underserved_nodes,
                        "jobs_services": service_nodes}
    distance_maps = {name: nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), dest, weight="weight")
                     for name, dest in destination_sets.items()}

    valid = corridors[corridors.snap_valid].copy()
    aggregates = {int(cid): {f"benefit_{name}": 0.0 for name in destination_sets} for cid in valid.corridor_id}
    target_nodes = set(valid.u.astype(int)) | set(valid.v.astype(int))
    for origin in origins.itertuples():
        bases = {name: distances.get(int(origin.graph_node), math.inf) for name, distances in distance_maps.items()}
        finite = [x for x in bases.values() if math.isfinite(x)]
        if not finite: continue
        local = nx.single_source_dijkstra_path_length(graph, int(origin.graph_node), cutoff=max(finite), weight="weight")
        if not target_nodes.intersection(local): continue
        for c in valid.itertuples():
            u, v = int(c.u), int(c.v)
            if u not in local and v not in local: continue
            for name, distances in distance_maps.items():
                base = bases[name]
                new = min(base,
                          local.get(u, math.inf) + c.cost_uv_m + distances.get(v, math.inf),
                          local.get(v, math.inf) + c.cost_vu_m + distances.get(u, math.inf))
                if new < base:
                    aggregates[int(c.corridor_id)][f"benefit_{name}"] += float(origin.population_est) * (base - new) / SPEED_M_PER_MIN
    benefits = pd.DataFrame.from_dict(aggregates, orient="index").rename_axis("corridor_id").reset_index()
    corridors = corridors.merge(benefits, on="corridor_id", how="left").fillna({f"benefit_{n}": 0 for n in destination_sets})
    for name in destination_sets:
        scale = corridors[f"benefit_{name}"].sum()
        corridors[f"normalized_{name}"] = corridors[f"benefit_{name}"] / scale if scale else 0
    corridors["composite_utility"] = sum(WEIGHTS[n] * corridors[f"normalized_{n}"] for n in WEIGHTS)
    corridors["utility_per_m"] = (corridors.composite_utility / corridors.intervention_length_m).replace([np.inf, -np.inf], np.nan).fillna(0)
    corridors["rank"] = corridors.utility_per_m.rank(ascending=False, method="min").astype("Int64")
    corridors = gpd.GeoDataFrame(corridors.drop(columns=["geometry", "connector_u", "connector_v"]),
                                 geometry="display_geometry", crs=CRS).rename_geometry("geometry").sort_values("rank")
    corridors.to_crs(4326).to_file(OUT / "corrected_corridor_ranking.gpkg", driver="GPKG")
    corridors.drop(columns="geometry").to_csv(OUT / "corrected_corridor_ranking.csv", index=False)
    origins.to_crs(4326).to_file(OUT / "corrected_zsj_accessibility.gpkg", driver="GPKG")

    top = corridors[corridors.snap_valid & (corridors.composite_utility > 0)].head(30)
    summary = {"method": "corrected-corridor-composite-v1", "historical_features": len(vanished),
               "continuous_corridors": len(corridors), "strict_snap_limit_m": SNAP_LIMIT_M,
               "valid_corridors": int(corridors.snap_valid.sum()),
               "positive_corridors": int((corridors.composite_utility > 0).sum()),
               "inaccessible_threshold_min": round(inaccessible_cutoff, 2),
               "top30": {"median_historical_length_m": round(float(top.historical_length_m.median()), 2),
                         "median_connector_length_m": round(float(top.connector_length_m.median()), 2),
                         "max_connector_length_m": round(float(top.connector_length_m.max()), 2),
                         "under_50m": int((top.historical_length_m < 50).sum())},
               "corrections": ["planned cycling generel excluded from baseline", "true street intersections noded",
                               "parallel source duplication removed", "20 m strict endpoint snap",
                               "snap connectors included in geometry and impedance", "touching fragments merged into corridors"]}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
