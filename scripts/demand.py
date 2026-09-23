from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import rasterio
from scipy.spatial import cKDTree
from shapely.geometry import Point


ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = ROOT / "data" / "derived" / "graph"
OUT_DIR = ROOT / "data" / "derived" / "analysis"
REPORT = ROOT / "reports" / "implementation_summary.json"
METRIC_CRS = 5514
SPEED_M_PER_MIN = 250.0  # 15 km/h reference cycling speed
QUALITY = {"streets": 1.00, "cycling_generel": 0.90, "cycling_routes": 0.75}
THRESHOLDS_MIN = (5, 10, 15)


def cycling_cost(length_m: float, grade: float, quality: float = 1.0) -> float:
    """Directional generalized distance in metres-equivalent."""
    uphill = max(grade, 0.0)
    downhill = abs(min(grade, 0.0))
    slope_multiplier = 1.0 + 8.0 * uphill + 1.5 * downhill
    return float(length_m * quality * slope_multiplier)


def sample_elevation(points: list[tuple[float, float]], raster_path: Path) -> np.ndarray:
    with rasterio.open(raster_path) as src:
        values = np.array([v[0] for v in src.sample(points)], dtype=float)
        if src.nodata is not None:
            values[values == src.nodata] = np.nan
    values[(values < 100) | (values > 1000)] = np.nan
    return values


def nearest_ids(xy: np.ndarray, node_xy: np.ndarray, node_ids: np.ndarray):
    distance, index = cKDTree(node_xy).query(xy, k=1)
    return node_ids[index], distance


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    edges = gpd.read_file(GRAPH_DIR / "prague_graph_edges.gpkg").to_crs(METRIC_CRS)
    nodes = gpd.read_file(GRAPH_DIR / "prague_graph_nodes.gpkg").to_crs(METRIC_CRS)
    terrain_path = ROOT / "data" / "cleaned" / "terrain" / "praha_dmr5g_8m.tif"

    node_ids = nodes["node_id"].astype(int).to_numpy()
    node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
    node_elevation = sample_elevation(list(map(tuple, node_xy)), terrain_path)
    elevation_by_id = dict(zip(node_ids, node_elevation))

    edges["elev_u_m"] = edges["u"].map(elevation_by_id)
    edges["elev_v_m"] = edges["v"].map(elevation_by_id)
    edges["grade_uv"] = (edges["elev_v_m"] - edges["elev_u_m"]) / edges["length_m"]
    edges["grade_uv"] = edges["grade_uv"].clip(-0.25, 0.25)
    edges["quality_factor"] = edges["source_layer"].map(QUALITY).fillna(1.0)
    edges["cost_uv_m"] = [cycling_cost(l, g if pd.notna(g) else 0, q) for l, g, q in zip(edges.length_m, edges.grade_uv, edges.quality_factor)]
    edges["cost_vu_m"] = [cycling_cost(l, -g if pd.notna(g) else 0, q) for l, g, q in zip(edges.length_m, edges.grade_uv, edges.quality_factor)]
    edges["time_uv_min"] = edges["cost_uv_m"] / SPEED_M_PER_MIN
    edges["time_vu_min"] = edges["cost_vu_m"] / SPEED_M_PER_MIN
    edge_path = OUT_DIR / "prague_edges_impedance.gpkg"
    edges.to_crs(4326).to_file(edge_path, driver="GPKG")
    edges.drop(columns="geometry").to_csv(OUT_DIR / "prague_edges_impedance.csv", index=False)

    graph = nx.DiGraph()
    for row in edges[["u", "v", "cost_uv_m", "cost_vu_m"]].itertuples(index=False):
        u, v = int(row.u), int(row.v)
        for a, b, cost in ((u, v, row.cost_uv_m), (v, u, row.cost_vu_m)):
            old = graph.get_edge_data(a, b, {}).get("weight", math.inf)
            if cost < old:
                graph.add_edge(a, b, weight=float(cost))

    stops = pd.read_csv(ROOT / "data" / "cleaned" / "gtfs" / "stops.csv", low_memory=False)
    stops = stops.dropna(subset=["stop_lon", "stop_lat"]).copy()
    if "location_type" in stops:
        stops = stops[stops["location_type"].fillna(0).astype(str).isin(["0", "0.0"])]
    stop_gdf = gpd.GeoDataFrame(stops, geometry=gpd.points_from_xy(stops.stop_lon, stops.stop_lat), crs=4326).to_crs(METRIC_CRS)
    stop_xy = np.column_stack([stop_gdf.geometry.x, stop_gdf.geometry.y])
    stop_gdf["graph_node"], stop_gdf["snap_distance_m"] = nearest_ids(stop_xy, node_xy, node_ids)
    stop_gdf = stop_gdf[stop_gdf.snap_distance_m <= 500].copy()
    destination_nodes = set(stop_gdf.graph_node.astype(int))
    distance_to_stop = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), destination_nodes, weight="weight")

    origins = gpd.read_file(ROOT / "data" / "cleaned" / "population" / "praha_mestske_casti_population.gpkg").to_crs(METRIC_CRS)
    origin_points = origins.geometry.representative_point()
    origin_xy = np.column_stack([origin_points.x, origin_points.y])
    origins["graph_node"], origins["snap_distance_m"] = nearest_ids(origin_xy, node_xy, node_ids)
    origins["baseline_cost_m"] = origins.graph_node.map(distance_to_stop)
    origins["baseline_time_min"] = origins.baseline_cost_m / SPEED_M_PER_MIN
    origins["reachable"] = origins.baseline_cost_m.notna()
    origins.to_crs(4326).to_file(OUT_DIR / "city_part_accessibility_baseline.gpkg", driver="GPKG")
    origins.drop(columns="geometry").to_csv(OUT_DIR / "city_part_accessibility_baseline.csv", index=False)

    # Reuse each origin's shortest-path tree across candidates.
    origin_distances = {
        int(row.graph_node): nx.single_source_dijkstra_path_length(graph, int(row.graph_node), weight="weight")
        for row in origins[["graph_node"]].drop_duplicates().itertuples(index=False)
    }

    candidates = gpd.read_file(ROOT / "data" / "cleaned" / "historical_paths" / "praha_vanished_paths.gpkg").to_crs(METRIC_CRS)
    candidates = candidates[candidates.geometry.notna() & ~candidates.geometry.is_empty].copy()
    candidates["candidate_id"] = np.arange(1, len(candidates) + 1)
    candidates["length_m"] = candidates.geometry.length
    start_xy = np.array([geom.coords[0] for geom in candidates.geometry])
    end_xy = np.array([geom.coords[-1] for geom in candidates.geometry])
    candidates["u"], candidates["snap_u_m"] = nearest_ids(start_xy, node_xy, node_ids)
    candidates["v"], candidates["snap_v_m"] = nearest_ids(end_xy, node_xy, node_ids)
    endpoint_elev = sample_elevation(list(map(tuple, np.vstack([start_xy, end_xy]))), terrain_path)
    z_u, z_v = np.split(endpoint_elev, 2)
    grades = np.clip((z_v - z_u) / candidates.length_m.to_numpy(), -0.25, 0.25)
    candidates["grade_uv"] = grades
    candidates["cost_uv_m"] = [cycling_cost(l, g if np.isfinite(g) else 0) for l, g in zip(candidates.length_m, grades)]
    candidates["cost_vu_m"] = [cycling_cost(l, -g if np.isfinite(g) else 0) for l, g in zip(candidates.length_m, grades)]
    candidates["snap_ok"] = (candidates.snap_u_m <= 100) & (candidates.snap_v_m <= 100) & (candidates.u != candidates.v)

    results = []
    for candidate in candidates.itertuples():
        total_saved = 0.0
        improved_population = 0.0
        threshold_gain = {t: 0.0 for t in THRESHOLDS_MIN}
        if not candidate.snap_ok:
            results.append({
                "candidate_id": candidate.candidate_id,
                "population_minutes_saved": 0.0,
                "improved_population": 0.0,
                **{f"new_population_within_{t}min": 0.0 for t in THRESHOLDS_MIN},
            })
            continue
        for origin in origins.itertuples():
            base = origin.baseline_cost_m
            dist = origin_distances[int(origin.graph_node)]
            via_uv = dist.get(int(candidate.u), math.inf) + candidate.cost_uv_m + distance_to_stop.get(int(candidate.v), math.inf)
            via_vu = dist.get(int(candidate.v), math.inf) + candidate.cost_vu_m + distance_to_stop.get(int(candidate.u), math.inf)
            new = min(base if pd.notna(base) else math.inf, via_uv, via_vu)
            if math.isfinite(new) and (pd.isna(base) or new < base - 1e-6):
                saved = (base - new) / SPEED_M_PER_MIN if pd.notna(base) else 0.0
                total_saved += float(origin.population_total) * max(saved, 0)
                improved_population += float(origin.population_total)
            for threshold in THRESHOLDS_MIN:
                limit = threshold * SPEED_M_PER_MIN
                if new <= limit and (pd.isna(base) or base > limit):
                    threshold_gain[threshold] += float(origin.population_total)
        results.append({
            "candidate_id": candidate.candidate_id,
            "population_minutes_saved": total_saved,
            "improved_population": improved_population,
            **{f"new_population_within_{t}min": threshold_gain[t] for t in THRESHOLDS_MIN},
        })
    result_df = pd.DataFrame(results)
    candidates = candidates.merge(result_df, on="candidate_id")
    candidates["benefit_per_m"] = candidates.population_minutes_saved / candidates.length_m
    candidates["feasibility_priority"] = candidates.benefit_per_m * np.where(candidates.vlastn_hmp == "ANO", 1.15, 1.0)
    candidates = candidates.sort_values(["snap_ok", "benefit_per_m"], ascending=False)
    candidates.to_crs(4326).to_file(OUT_DIR / "vanished_paths_scored.gpkg", driver="GPKG")
    candidates.drop(columns="geometry").to_csv(OUT_DIR / "vanished_paths_scored.csv", index=False)

    valid_elevation = int(edges[["elev_u_m", "elev_v_m"]].notna().all(axis=1).sum())
    pop_total = float(origins.population_total.sum())
    summary = {
        "method_version": "mvp-v1",
        "parameters": {"metric_crs": "EPSG:5514", "speed_kmh": 15, "quality_factors": QUALITY,
                       "candidate_snap_tolerance_m": 100, "stop_snap_tolerance_m": 500,
                       "accessibility_thresholds_min": THRESHOLDS_MIN},
        "impedance": {"edges": int(len(edges)), "edges_with_valid_elevation": valid_elevation,
                      "coverage_pct": round(100 * valid_elevation / len(edges), 2)},
        "destinations": {"gtfs_stops_loaded": int(len(stops)), "gtfs_stops_snapped": int(len(stop_gdf)),
                         "unique_destination_nodes": len(destination_nodes)},
        "baseline": {"origins": int(len(origins)), "reachable_origins": int(origins.reachable.sum()),
                     "population_total": int(pop_total),
                     **{f"population_within_{t}min": int(origins.loc[origins.baseline_time_min <= t, "population_total"].sum()) for t in THRESHOLDS_MIN}},
        "candidates": {"evaluated": int(len(candidates)), "snap_valid": int(candidates.snap_ok.sum()),
                       "with_positive_benefit": int((candidates.population_minutes_saved > 0).sum()),
                       "total_length_km": round(float(candidates.length_m.sum() / 1000), 2)},
        "outputs": {"impedance_edges": str(edge_path.relative_to(ROOT)),
                    "accessibility_baseline": "data/derived/analysis/city_part_accessibility_baseline.gpkg",
                    "scored_candidates": "data/derived/analysis/vanished_paths_scored.gpkg"},
    }
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
