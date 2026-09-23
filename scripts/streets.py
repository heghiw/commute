from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import pyogrio
import rasterio
from shapely import get_parts
from shapely.geometry import LineString, Point


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/derived/corrected_graph"
REPORT = ROOT / "reports/corrected_graph_summary.json"
CRS = 5514
CYCLE_MATCH_M = 12.0
SPEED_M_PER_MIN = 250.0


def cycling_cost(length_m: float, grade: float, cycle_existing: bool) -> float:
    quality = 0.75 if cycle_existing else 1.0
    return float(length_m * quality * (1 + 8 * max(grade, 0) + 1.5 * abs(min(grade, 0))))


def sample(points: list[tuple[float, float]]) -> np.ndarray:
    path = ROOT / "data/cleaned/terrain/praha_dmr5g_8m.tif"
    with rasterio.open(path) as src:
        values = np.array([v[0] for v in src.sample(points)], dtype=float)
    values[(values < 100) | (values > 1000)] = np.nan
    return values


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    streets = gpd.read_file(ROOT / "data/cleaned/network/praha_streets_ruian.gpkg").to_crs(CRS)
    cycling = gpd.read_file(ROOT / "data/cleaned/network/praha_cycling_routes.gpkg").to_crs(CRS)

    # Node planar intersections and remove duplicate geometry.
    noded = streets.geometry.union_all()
    records = []
    for line in get_parts(noded):
        if line.geom_type != "LineString":
            continue
        coords = list(line.coords)
        for a, b in zip(coords[:-1], coords[1:]):
            segment = LineString([a, b])
            if segment.length > .05:
                records.append({"geometry": segment, "length_m": float(segment.length)})
    edges = gpd.GeoDataFrame(records, crs=CRS)
    cycle_union = cycling.geometry.union_all()
    edges["distance_to_cycle_m"] = edges.geometry.interpolate(.5, normalized=True).distance(cycle_union)
    edges["cycle_existing"] = edges.distance_to_cycle_m <= CYCLE_MATCH_M

    # Match OSM tags to the street segments.
    osm = pyogrio.read_dataframe(ROOT / "data/cleaned/network/praha-latest.osm.pbf", layer="lines",
                                 columns=["osm_id", "highway", "other_tags"])
    osm = osm[osm.highway.notna()].to_crs(CRS)
    midpoints = gpd.GeoDataFrame({"edge_row": edges.index}, geometry=edges.geometry.interpolate(.5, normalized=True), crs=CRS)
    matched = gpd.sjoin_nearest(midpoints, osm[["osm_id", "highway", "other_tags", "geometry"]],
                                how="left", max_distance=15, distance_col="osm_distance_m")
    matched = matched.sort_values("osm_distance_m").drop_duplicates("edge_row").set_index("edge_row")
    edges["osm_highway"] = matched.highway.reindex(edges.index)
    edges["osm_tags"] = matched.other_tags.reindex(edges.index)
    edges["osm_distance_m"] = matched.osm_distance_m.reindex(edges.index)
    tags = edges.osm_tags.fillna("")
    forbidden_class = edges.osm_highway.isin(["motorway", "motorway_link", "trunk", "trunk_link", "steps", "raceway"])
    forbidden_tag = tags.str.contains('"bicycle"=>"no"', regex=False) | tags.str.contains('"access"=>"no"', regex=False)
    explicit_bike = tags.str.contains('"bicycle"=>"yes"', regex=False) | tags.str.contains('"bicycle"=>"designated"', regex=False)
    edges["bike_allowed"] = ~(forbidden_class | (forbidden_tag & ~explicit_bike)) | edges.cycle_existing
    edges["grade_separated"] = (tags.str.contains('"bridge"=>"yes"', regex=False) |
                                  tags.str.contains('"tunnel"=>"yes"', regex=False))
    edges = edges[edges.bike_allowed].copy().reset_index(drop=True)

    node_lookup: dict[tuple[float, float], int] = {}
    node_rows = []
    def node_id(coord) -> int:
        key = (round(float(coord[0]), 2), round(float(coord[1]), 2))
        if key not in node_lookup:
            node_lookup[key] = len(node_lookup) + 1
            node_rows.append({"node_id": node_lookup[key], "geometry": Point(key)})
        return node_lookup[key]
    uv = [(node_id(row.geometry.coords[0]), node_id(row.geometry.coords[-1])) for row in edges.itertuples()]
    edges["u"] = [x[0] for x in uv]; edges["v"] = [x[1] for x in uv]
    nodes = gpd.GeoDataFrame(node_rows, crs=CRS)
    elevation = sample([(p.x, p.y) for p in nodes.geometry])
    nodes["elevation_m"] = elevation
    elev = dict(zip(nodes.node_id, nodes.elevation_m))
    edges["elev_u_m"] = edges.u.map(elev); edges["elev_v_m"] = edges.v.map(elev)
    edges["grade_uv"] = ((edges.elev_v_m - edges.elev_u_m) / edges.length_m).clip(-.25, .25)
    edges["cost_uv_m"] = [cycling_cost(l, g if pd.notna(g) else 0, c) for l, g, c in zip(edges.length_m, edges.grade_uv, edges.cycle_existing)]
    edges["cost_vu_m"] = [cycling_cost(l, -g if pd.notna(g) else 0, c) for l, g, c in zip(edges.length_m, edges.grade_uv, edges.cycle_existing)]
    edges["edge_id"] = np.arange(1, len(edges) + 1)

    graph = nx.Graph()
    graph.add_edges_from((int(r.u), int(r.v)) for r in edges.itertuples())
    components = sorted((len(c) for c in nx.connected_components(graph)), reverse=True)
    nodes.to_crs(4326).to_file(OUT / "nodes.gpkg", driver="GPKG")
    edges.to_crs(4326).to_file(OUT / "edges.gpkg", driver="GPKG")
    edges.drop(columns="geometry").to_csv(OUT / "edges.csv", index=False)
    summary = {"method": "noded-street-base-v1", "nodes": len(nodes), "edges": len(edges),
               "components": len(components), "largest_component_nodes": components[0],
               "largest_component_pct": round(100 * components[0] / len(nodes), 2),
               "current_cycle_edges": int(edges.cycle_existing.sum()), "current_cycle_match_m": CYCLE_MATCH_M,
               "planned_generel_in_baseline": False,
               "osm_access_filter": True, "grade_separated_edges": int(edges.grade_separated.sum()),
               "notes": ["Street geometries are noded at true intersections using union_all.",
                         "Coincident source geometries are represented once.",
                         "Current cycling routes modify street cost instead of creating parallel edges."]}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
