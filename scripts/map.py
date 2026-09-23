from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Transformer

try:
    from scripts.stations import load_destination_points
except ModuleNotFoundError:
    from stations import load_destination_points


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/prague_graph_interactive.html"
TEMPLATE = ROOT / "scripts/templates/map.html"


def coordinates(geometry) -> list[list[float]]:
    return [[round(x, 6), round(y, 6)] for x, y, *_ in geometry.coords]


def status(row: pd.Series) -> str:
    if float(row.parallel_existing_road_fraction) > 0.60:
        return "parallel"
    if not bool(row.routable_corridor):
        return "unconnected"
    positive = pd.notna(row.composite_utility) and float(row.composite_utility) > 0
    if positive and bool(row.material_candidate):
        return "screened_link"
    if positive and bool(row.planning_candidate) and bool(row.requires_crossing_review):
        return "crossing_review"
    return "other"


def main() -> None:
    edges = gpd.read_file(ROOT / "data/derived/corrected_graph/edges.gpkg").to_crs(4326)
    all_corridors = gpd.read_file(ROOT / "data/derived/corrected_analysis/edge_connected_corridors.gpkg").to_crs(5514)
    scored = gpd.read_file(ROOT / "data/derived/corrected_analysis/edge_connected_corridor_ranking.gpkg").drop(columns="geometry")
    score_fields = ["corridor_id", "composite_utility", "benefit_strategic_transport", "benefit_metro", "benefit_train",
                    "benefit_underserved_destinations", "benefit_jobs_services", "material_candidate",
                    "planning_candidate", "requires_crossing_review", "max_detour_saved_m",
                    "max_detour_ratio", "parallel_existing_road_fraction"]
    all_corridors = all_corridors.drop(columns=[c for c in score_fields[1:] if c in all_corridors.columns])
    all_corridors = all_corridors.merge(scored[score_fields], on="corridor_id", how="left")
    historical_lines = gpd.GeoSeries.from_wkt(all_corridors.historical_wkt, crs=5514).to_crs(4326)
    transformer = Transformer.from_crs(5514, 4326, always_xy=True)

    roads = []
    for edge in edges.itertuples():
        start, end = list(edge.geometry.coords)[0], list(edge.geometry.coords)[-1]
        roads.append([*start[:2], *end[:2], bool(edge.cycle_existing), round(float(edge.length_m), 1)])

    corridors = []
    for row, line in zip(all_corridors.itertuples(), historical_lines):
        path = coordinates(line)
        access = [item for item in json.loads(row.access_json) if item.get("active")]
        positions = [[round(v, 6) for v in transformer.transform(item["x"], item["y"])] for item in access]
        endpoint_spurs, proximity_spurs = [], []
        for item, position in zip(access, positions):
            if item["kind"] == "endpoint" and item["connector_m"] > 0.05:
                endpoint_spurs.append([path[0] if item["measure_m"] < row.historical_length_m / 2 else path[-1], position])
            elif item["kind"] == "proximity":
                on_line = [round(v, 6) for v in transformer.transform(item["corridor_x"], item["corridor_y"])]
                proximity_spurs.append([on_line, position])
        item = pd.Series(row._asdict())
        corridors.append({
            "id": int(row.corridor_id), "path": path, "gaps": endpoint_spurs, "proximity": proximity_spurs,
            "crossings": [p for a, p in zip(access, positions) if a.get("unsafe_major_crossing", False)],
            "status": status(item), "tier": str(row.intervention_tier),
            "routable": bool(row.routable_corridor),
            "length": round(float(row.historical_length_m), 1),
            "parallel": round(float(row.parallel_existing_road_fraction or 0), 3),
            "saved": round(float(row.max_detour_saved_m), 1) if pd.notna(row.max_detour_saved_m) else 0,
            "detour": round(float(row.max_detour_ratio), 2) if pd.notna(row.max_detour_ratio) else 0,
            "composite": float(row.composite_utility) if pd.notna(row.composite_utility) else 0,
            "strategic": float(row.benefit_strategic_transport) if pd.notna(row.benefit_strategic_transport) else 0,
            "metro": float(row.benefit_metro) if pd.notna(row.benefit_metro) else 0,
            "train": float(row.benefit_train) if pd.notna(row.benefit_train) else 0,
            "underserved": float(row.benefit_underserved_destinations) if pd.notna(row.benefit_underserved_destinations) else 0,
            "services": float(row.benefit_jobs_services) if pd.notna(row.benefit_jobs_services) else 0,
        })

    counts = pd.Series([item["status"] for item in corridors]).value_counts().to_dict()
    station_points = load_destination_points()
    stations = [
        {"position": [round(point.x, 6), round(point.y, 6)], "kind": kind, "name": row["name"]}
        for kind, frame in station_points.items()
        for point, (_, row) in zip(frame.to_crs(4326).geometry, frame.iterrows())
    ]
    metrics = {"corridors": len(corridors), "roads": len(roads),
               "routable": sum(item["routable"] for item in corridors), "counts": counts}
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__ROADS_JSON__", json.dumps(roads, separators=(",", ":")))
    html = html.replace("__CORRIDORS_JSON__", json.dumps(corridors, separators=(",", ":")))
    html = html.replace("__STATIONS_JSON__", json.dumps(stations, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__METRICS_JSON__", json.dumps(metrics, separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(corridors)} corridors, {len(roads)} roads, {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
