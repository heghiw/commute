from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CRS = 5514
OUT_TABLE = ROOT / "reports/transit_context_corridors.csv"
OUT_SUMMARY = ROOT / "reports/transit_context_summary.json"


def nearest_distances(source: gpd.GeoSeries, targets: gpd.GeoSeries) -> np.ndarray:
    spatial = targets.sindex
    distances = []
    for geometry in source:
        if geometry is None or geometry.is_empty:
            distances.append(np.nan)
            continue
        _, distance = spatial.nearest(geometry, return_all=False, return_distance=True)
        distances.append(float(distance[0]) if len(distance) else np.nan)
    return np.asarray(distances)


def main() -> None:
    corridors = gpd.read_file(ROOT / "data/derived/corrected_analysis/edge_connected_corridors.gpkg")
    historical = gpd.GeoSeries.from_wkt(corridors.historical_wkt, crs=CRS)
    pid_lines = gpd.read_file(ROOT / "data/processed/network/pid_lines_wgs84.geojson").to_crs(CRS)
    metro = gpd.read_file(ROOT / "data/processed/network/praha_metro_entrances.geojson").to_crs(CRS)

    result = pd.DataFrame({
        "corridor_id": corridors.corridor_id.astype(int),
        "nearest_pid_line_m": nearest_distances(historical, pid_lines.geometry),
        "nearest_metro_entrance_m": nearest_distances(historical, metro.geometry),
    })
    result["near_pid_line_50m"] = result.nearest_pid_line_m.le(50)
    result["near_metro_entrance_300m"] = result.nearest_metro_entrance_m.le(300)
    result.sort_values("corridor_id").to_csv(OUT_TABLE, index=False)

    summary = {
        "pid_line_features": int(len(pid_lines)),
        "pid_route_ids": int(pid_lines.route_id.nunique()),
        "pid_route_types": {str(k): int(v) for k, v in pid_lines.route_type.astype(str).value_counts().items()},
        "metro_entrances": int(len(metro)),
        "metro_stations": int(metro.uzel_nazev.nunique()),
        "corridors": int(len(result)),
        "corridors_near_pid_line_50m": int(result.near_pid_line_50m.sum()),
        "corridors_near_metro_entrance_300m": int(result.near_metro_entrance_300m.sum()),
        "median_nearest_pid_line_m": float(result.nearest_pid_line_m.median()),
        "median_nearest_metro_entrance_m": float(result.nearest_metro_entrance_m.median()),
        "method": "minimum planar distance from historical corridor geometry in EPSG:5514",
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_TABLE.relative_to(ROOT)} and {OUT_SUMMARY.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
