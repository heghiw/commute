from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CLEANED_DIR = ROOT / "data" / "cleaned"
REPORTS_DIR = ROOT / "reports"
METRIC_CRS = 5514


def load_gdf(relative_path: str) -> gpd.GeoDataFrame:
    return gpd.read_file(CLEANED_DIR / relative_path)


def length_km(gdf: gpd.GeoDataFrame) -> float:
    return float(gdf.to_crs(METRIC_CRS).length.sum() / 1000.0)


def area_km2(gdf: gpd.GeoDataFrame) -> pd.Series:
    return gdf.to_crs(METRIC_CRS).area / 1_000_000.0


def terrain_summary() -> dict[str, object]:
    terrain_path = CLEANED_DIR / "terrain" / "praha_dmr5g_8m.tif"
    summary = {
        "path": str(terrain_path.relative_to(ROOT)),
        "bytes": terrain_path.stat().st_size,
    }

    try:
        import rasterio
    except ImportError:
        summary["metadata"] = "rasterio not installed; raster metadata skipped"
        return summary

    with rasterio.open(terrain_path) as dataset:
        summary["metadata"] = {
            "crs": str(dataset.crs),
            "width": dataset.width,
            "height": dataset.height,
            "count": dataset.count,
            "dtype": dataset.dtypes[0],
            "bounds": list(dataset.bounds),
            "resolution": list(dataset.res),
        }
    return summary


def top_records(df: pd.DataFrame, value_column: str, name_column: str, limit: int = 10) -> list[dict[str, object]]:
    top = df.sort_values(value_column, ascending=False).head(limit)
    return [
        {
            name_column: row[name_column],
            value_column: None if pd.isna(row[value_column]) else float(row[value_column]),
        }
        for _, row in top.iterrows()
    ]


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    cycling_routes = load_gdf("network/praha_cycling_routes.gpkg")
    cycling_generel = load_gdf("network/praha_cycling_generel.gpkg")
    streets = load_gdf("network/praha_streets_ruian.gpkg")
    vanished_paths = load_gdf("historical_paths/praha_vanished_paths.gpkg")
    zsj = load_gdf("boundaries/praha_zsj.gpkg")
    buc = load_gdf("boundaries/praha_buc.gpkg")
    city_parts = load_gdf("population/praha_mestske_casti_population.gpkg")

    gtfs_stops = pd.read_csv(CLEANED_DIR / "gtfs" / "stops.csv", low_memory=False)
    gtfs_routes = pd.read_csv(CLEANED_DIR / "gtfs" / "routes.csv", low_memory=False)
    gtfs_trips = pd.read_csv(CLEANED_DIR / "gtfs" / "trips.csv", low_memory=False)

    city_parts = city_parts.copy()
    city_parts["area_km2"] = area_km2(city_parts)
    city_parts["population_density_per_km2"] = city_parts["population_total"] / city_parts["area_km2"]

    summary = {
        "network": {
            "cycling_routes": {
                "features": int(len(cycling_routes)),
                "total_length_km": round(length_km(cycling_routes), 2),
                "unique_route_labels": int(cycling_routes["cislo_tras"].dropna().nunique()),
            },
            "cycling_generel": {
                "features": int(len(cycling_generel)),
                "total_length_km": round(length_km(cycling_generel), 2),
                "unique_route_labels": int(cycling_generel["nazev"].dropna().nunique()),
            },
            "streets": {
                "features": int(len(streets)),
                "total_length_km": round(length_km(streets), 2),
                "unique_street_names": int(streets["nazev"].dropna().nunique()),
            },
            "osm": {
                "path": str((CLEANED_DIR / 'network' / 'praha-latest.osm.pbf').relative_to(ROOT)),
                "bytes": (CLEANED_DIR / 'network' / 'praha-latest.osm.pbf').stat().st_size,
            },
        },
        "historical_paths": {
            "features": int(len(vanished_paths)),
            "total_length_km": round(length_km(vanished_paths), 2),
            "owned_by_hmp_count": int((vanished_paths["vlastn_hmp"] == "ANO").sum()),
            "flagged_in_cycling_generel_count": int((vanished_paths["cyklo_gen"] == "ANO").sum()),
        },
        "boundaries": {
            "city_parts": int(len(city_parts)),
            "zsj": int(len(zsj)),
            "buc": int(len(buc)),
        },
        "population": {
            "city_parts_with_population": int(city_parts["population_total"].notna().sum()),
            "total_population": int(city_parts["population_total"].sum()),
            "total_area_km2": round(float(city_parts["area_km2"].sum()), 2),
            "mean_density_per_km2": round(float(city_parts["population_density_per_km2"].mean()), 2),
            "top_city_parts_by_population": top_records(city_parts, "population_total", "city_part_name", limit=10),
            "top_city_parts_by_density": top_records(city_parts, "population_density_per_km2", "city_part_name", limit=10),
        },
        "gtfs": {
            "stops": int(len(gtfs_stops)),
            "routes": int(len(gtfs_routes)),
            "trips": int(len(gtfs_trips)),
            "route_types": sorted(gtfs_routes["route_type"].dropna().astype(str).unique().tolist()),
        },
        "terrain": terrain_summary(),
    }

    report_path = REPORTS_DIR / "analysis_summary.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Prague commuting dataset summary")
    print(f"Cycling routes: {summary['network']['cycling_routes']['features']} features, {summary['network']['cycling_routes']['total_length_km']} km")
    print(f"Cycling generel: {summary['network']['cycling_generel']['features']} features, {summary['network']['cycling_generel']['total_length_km']} km")
    print(f"Street network: {summary['network']['streets']['features']} features, {summary['network']['streets']['total_length_km']} km")
    print(f"Vanished paths: {summary['historical_paths']['features']} features, {summary['historical_paths']['total_length_km']} km")
    print(f"City parts with population: {summary['population']['city_parts_with_population']} / {summary['boundaries']['city_parts']}")
    print(f"Total city-part population: {summary['population']['total_population']}")
    print(f"GTFS stops/routes/trips: {summary['gtfs']['stops']} / {summary['gtfs']['routes']} / {summary['gtfs']['trips']}")
    print(f"Analysis report written to: {report_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()