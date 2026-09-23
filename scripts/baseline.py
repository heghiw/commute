from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "derived" / "analysis"
GRAPH_DIR = ROOT / "data" / "derived" / "graph"
REPORT = ROOT / "reports" / "refined_analysis_summary.json"
CRS = 5514
SPEED_M_PER_MIN = 250.0
CATCHMENT_M = 1500.0


def proportional_allocation(total: float, areas: pd.Series) -> pd.Series:
    """Allocate a known total proportionally while preserving the total."""
    weights = areas.clip(lower=0).fillna(0)
    if weights.sum() == 0:
        return pd.Series(total / len(weights), index=weights.index)
    return total * weights / weights.sum()


def main() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    nodes = gpd.read_file(GRAPH_DIR / "prague_graph_nodes.gpkg").to_crs(CRS)
    edges = pd.read_csv(ANALYSIS_DIR / "prague_edges_impedance.csv", low_memory=False)
    candidates = gpd.read_file(ANALYSIS_DIR / "vanished_paths_scored.gpkg").to_crs(CRS)
    city = gpd.read_file(ROOT / "data/cleaned/population/praha_mestske_casti_population.gpkg").to_crs(CRS)
    zsj = gpd.read_file(ROOT / "data/cleaned/boundaries/praha_zsj.gpkg").to_crs(CRS)

    # Allocate city-part population to ZSJ by area.
    zsj = zsj.copy()
    zsj["area_km2"] = zsj.area / 1_000_000
    points = gpd.GeoDataFrame(zsj[["kod", "nazev", "area_km2"]], geometry=zsj.representative_point(), crs=CRS)
    lookup = gpd.sjoin(points, city[["city_part_name", "population_total", "geometry"]], predicate="within", how="left")
    zsj = zsj.merge(lookup[["kod", "city_part_name", "population_total"]], on="kod", how="left")
    zsj["population_est"] = 0.0
    for _, indices in zsj.dropna(subset=["city_part_name"]).groupby("city_part_name").groups.items():
        idx = list(indices)
        zsj.loc[idx, "population_est"] = proportional_allocation(float(zsj.loc[idx, "population_total"].iloc[0]), zsj.loc[idx, "area_km2"])

    node_ids = nodes.node_id.astype(int).to_numpy()
    node_xy = np.column_stack([nodes.geometry.x, nodes.geometry.y])
    tree = cKDTree(node_xy)
    zsj_points = zsj.representative_point()
    zsj_xy = np.column_stack([zsj_points.x, zsj_points.y])
    zsj["snap_distance_m"], ni = tree.query(zsj_xy, k=1)
    zsj["graph_node"] = node_ids[ni]

    # Select rail and high-service transit stops.
    stops = pd.read_csv(ROOT / "data/cleaned/gtfs/stops.csv", low_memory=False)
    route_stops = pd.read_csv(ROOT / "data/cleaned/gtfs/route_stops.csv", low_memory=False)
    routes = pd.read_csv(ROOT / "data/cleaned/gtfs/routes.csv", low_memory=False)
    served = route_stops[["route_id", "stop_id"]].drop_duplicates().merge(routes[["route_id", "route_type"]], on="route_id")
    service = served.groupby("stop_id").agg(route_count=("route_id", "nunique"),
                                             mode_count=("route_type", "nunique"),
                                             rail_metro=("route_type", lambda x: x.astype(str).isin(["1", "2"]).any())).reset_index()
    stops = stops.merge(service, on="stop_id", how="inner")
    stops = stops[(stops.rail_metro) | (stops.route_count >= 5) | (stops.mode_count >= 3)].dropna(subset=["stop_lon", "stop_lat"])
    stop_gdf = gpd.GeoDataFrame(stops, geometry=gpd.points_from_xy(stops.stop_lon, stops.stop_lat), crs=4326).to_crs(CRS)
    stop_xy = np.column_stack([stop_gdf.geometry.x, stop_gdf.geometry.y])
    stop_gdf["snap_distance_m"], si = tree.query(stop_xy, k=1)
    stop_gdf["graph_node"] = node_ids[si]
    stop_gdf = stop_gdf[stop_gdf.snap_distance_m <= 500].copy()
    destination_nodes = set(stop_gdf.graph_node.astype(int))

    graph = nx.DiGraph()
    for row in edges[["u", "v", "cost_uv_m", "cost_vu_m"]].itertuples(index=False):
        for a, b, cost in ((int(row.u), int(row.v), row.cost_uv_m), (int(row.v), int(row.u), row.cost_vu_m)):
            if cost < graph.get_edge_data(a, b, {}).get("weight", math.inf):
                graph.add_edge(a, b, weight=float(cost))
    dist = nx.multi_source_dijkstra_path_length(graph.reverse(copy=False), destination_nodes, weight="weight")
    zsj["strategic_cost_m"] = zsj.graph_node.map(dist)
    zsj["strategic_time_min"] = zsj.strategic_cost_m / SPEED_M_PER_MIN
    zsj.to_crs(4326).to_file(ANALYSIS_DIR / "zsj_accessibility_baseline.gpkg", driver="GPKG")
    zsj.drop(columns="geometry").to_csv(ANALYSIS_DIR / "zsj_accessibility_baseline.csv", index=False)

    # Estimate exposure within 1.5 km of each candidate.
    point_tree = cKDTree(zsj_xy)
    local_population, endpoint_gain = [], []
    for c in candidates.itertuples():
        midpoint = c.geometry.interpolate(0.5, normalized=True)
        nearby = point_tree.query_ball_point([midpoint.x, midpoint.y], CATCHMENT_M)
        local_population.append(float(zsj.iloc[nearby].population_est.sum()))
        du, dv = dist.get(int(c.u), math.inf), dist.get(int(c.v), math.inf)
        gain = max(0.0, du - (c.cost_uv_m + dv), dv - (c.cost_vu_m + du)) if c.snap_ok else 0.0
        endpoint_gain.append(gain / SPEED_M_PER_MIN if math.isfinite(gain) else 0.0)
    candidates["local_population_1500m"] = local_population
    candidates["endpoint_gain_min"] = endpoint_gain
    candidates["screening_benefit"] = candidates.local_population_1500m * candidates.endpoint_gain_min
    candidates["screening_benefit_per_m"] = candidates.screening_benefit / candidates.length_m
    candidates = candidates.sort_values(["snap_ok", "screening_benefit_per_m"], ascending=False)
    candidates.to_crs(4326).to_file(ANALYSIS_DIR / "vanished_paths_refined_screening.gpkg", driver="GPKG")
    candidates.drop(columns="geometry").to_csv(ANALYSIS_DIR / "vanished_paths_refined_screening.csv", index=False)

    allocated = float(zsj.population_est.sum())
    summary = {
        "method_version": "refined-screening-v2",
        "zsj": {"zones": int(len(zsj)), "zones_assigned": int(zsj.city_part_name.notna().sum()),
                "allocated_population": round(allocated), "source_population": int(city.population_total.sum()),
                "allocation_error": round(allocated - float(city.population_total.sum()), 6)},
        "strategic_gtfs": {"stops_selected": int(len(stops)), "stops_snapped": int(len(stop_gdf)),
                           "destination_nodes": len(destination_nodes)},
        "accessibility": {"reachable_zsj": int(zsj.strategic_cost_m.notna().sum()),
                          "median_time_min": round(float(zsj.strategic_time_min.median()), 2),
                          "population_within_10min": round(float(zsj.loc[zsj.strategic_time_min <= 10, "population_est"].sum())),
                          "population_within_20min": round(float(zsj.loc[zsj.strategic_time_min <= 20, "population_est"].sum()))},
        "screening": {"candidates": int(len(candidates)), "snap_valid": int(candidates.snap_ok.sum()),
                      "positive_endpoint_gain": int((candidates.endpoint_gain_min > 0).sum()),
                      "catchment_m": CATCHMENT_M},
        "limitations": ["ZSJ population is area-allocated within city parts, not observed ZSJ population.",
                        "Candidate screening uses endpoint access gain times nearby population; it is not a full OD assignment."],
    }
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
