from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
CLEANED_DIR = ROOT / "data" / "cleaned"


def snake_case(value: str) -> str:
    value = value.replace(".", "_").replace("/", "_")
    value = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip())
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_").lower()


def clean_text(value):
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


def join_name(value):
    value = clean_text(value)
    if not isinstance(value, str):
        return value
    value = value.casefold()
    value = re.sub(r"\s+", " ", value)
    return value


def ensure_dirs() -> dict[str, Path]:
    if not PROCESSED_DIR.exists():
        raise FileNotFoundError(f"Processed data directory not found: {PROCESSED_DIR}")

    dirs = {
        "root": CLEANED_DIR,
        "network": CLEANED_DIR / "network",
        "boundaries": CLEANED_DIR / "boundaries",
        "historical": CLEANED_DIR / "historical_paths",
        "terrain": CLEANED_DIR / "terrain",
        "population": CLEANED_DIR / "population",
        "gtfs": CLEANED_DIR / "gtfs",
        "metadata": CLEANED_DIR / "metadata",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [snake_case(str(column)) for column in normalized.columns]
    for column in normalized.columns:
        normalized[column] = normalized[column].map(clean_text)
    return normalized


def normalize_geodataframe(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    normalized = gdf.copy()
    normalized.columns = [snake_case(str(column)) for column in normalized.columns]
    for column in normalized.columns:
        if column != "geometry":
            normalized[column] = normalized[column].map(clean_text)

    for column in list(normalized.columns):
        if column == "geometry":
            continue
        if column.startswith("plati") or column.startswith("datum"):
            series = pd.to_numeric(normalized[column], errors="coerce")
            if series.notna().any():
                normalized[column] = pd.to_datetime(series, unit="ms", errors="coerce").dt.strftime("%Y-%m-%d")

    if normalized.crs is None:
        normalized = normalized.set_crs(5514)
    if normalized.crs.to_epsg() != 4326:
        normalized = normalized.to_crs(4326)

    return normalized


def add_join_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    normalized = gdf.copy()
    if "nazev_mc" in normalized.columns:
        normalized["city_part_name"] = normalized["nazev_mc"].map(clean_text)
        normalized["join_name"] = normalized["nazev_mc"].map(join_name)
    elif "nazev" in normalized.columns:
        normalized["join_name"] = normalized["nazev"].map(join_name)
    return normalized


def write_geodata(source: Path, destination: Path, *, expected_crs: int | None = None) -> dict[str, object]:
    gdf = gpd.read_file(source)
    if gdf.crs is None and expected_crs is not None:
        gdf = gdf.set_crs(expected_crs)
    normalized = normalize_geodataframe(gdf)
    normalized.to_file(destination, driver="GPKG")
    bounds = normalized.total_bounds.tolist()
    return {
        "source": str(source.relative_to(ROOT)),
        "output": str(destination.relative_to(ROOT)),
        "feature_count": int(len(normalized)),
        "columns": [column for column in normalized.columns if column != "geometry"],
        "crs": str(normalized.crs),
        "bounds": bounds,
    }


def clean_population_workbook(source: Path, destination: Path) -> dict[str, object]:
    raw = pd.read_excel(source, sheet_name="DATA", header=None)
    rows = raw.loc[7:, 1:7].copy()
    rows.columns = [
        "area_name",
        "population_total",
        "residents_same_city_part",
        "residents_other_city_part_same_district",
        "residents_other_district_same_region",
        "residents_other_region",
        "residents_without_registered_residence",
    ]
    rows = rows[rows["area_name"].notna()].copy()
    rows["area_name"] = rows["area_name"].map(clean_text)
    rows = rows[rows["area_name"].str.startswith("Praha")].copy()
    rows["area_level"] = rows["area_name"].map(lambda value: "city" if value == "Praha" else "city_part")
    rows["district_number"] = rows["area_name"].str.extract(r"Praha\s+(\d+)$")
    rows["join_name"] = rows["area_name"].map(join_name)

    numeric_columns = [
        "population_total",
        "residents_same_city_part",
        "residents_other_city_part_same_district",
        "residents_other_district_same_region",
        "residents_other_region",
        "residents_without_registered_residence",
    ]
    for column in numeric_columns:
        rows[column] = pd.to_numeric(rows[column].replace("-", pd.NA), errors="coerce")

    rows.to_csv(destination, index=False)
    return {
        "source": str(source.relative_to(ROOT)),
        "output": str(destination.relative_to(ROOT)),
        "row_count": int(len(rows)),
        "columns": list(rows.columns),
    }


def join_city_parts_with_population(boundaries_path: Path, population_path: Path, destination: Path) -> dict[str, object]:
    boundaries = gpd.read_file(boundaries_path)
    population = pd.read_csv(population_path)

    boundaries = add_join_columns(boundaries)
    city_parts = population[population["area_level"] == "city_part"].copy()

    joined = boundaries.merge(city_parts, on="join_name", how="left", validate="1:1")
    joined.to_file(destination, driver="GPKG")

    matched = int(joined["population_total"].notna().sum())
    return {
        "source_boundaries": str(boundaries_path.relative_to(ROOT)),
        "source_population": str(population_path.relative_to(ROOT)),
        "output": str(destination.relative_to(ROOT)),
        "feature_count": int(len(joined)),
        "matched_population_rows": matched,
        "unmatched_boundary_rows": int(len(joined) - matched),
    }


def clean_gtfs_directory(source_dir: Path, destination_dir: Path) -> dict[str, object]:
    tables: dict[str, int] = {}
    for source in sorted(source_dir.glob("*.txt")):
        df = pd.read_csv(source, low_memory=False)
        df = normalize_frame(df)
        output_name = source.name.replace(".txt", ".csv")
        df.to_csv(destination_dir / output_name, index=False)
        tables[output_name] = int(len(df))
    return {
        "source": str(source_dir.relative_to(ROOT)),
        "output": str(destination_dir.relative_to(ROOT)),
        "tables": tables,
    }


def copy_asset(source: Path, destination: Path) -> dict[str, object]:
    shutil.copy2(source, destination)
    return {
        "source": str(source.relative_to(ROOT)),
        "output": str(destination.relative_to(ROOT)),
        "bytes": destination.stat().st_size,
    }


def main() -> None:
    dirs = ensure_dirs()

    summary: dict[str, object] = {"geodata": {}, "tables": {}, "assets": {}}

    geodata_sources = [
        (PROCESSED_DIR / "network" / "praha_cycling_routes.geojson", dirs["network"] / "praha_cycling_routes.gpkg", None),
        (PROCESSED_DIR / "network" / "praha_cycling_generel.geojson", dirs["network"] / "praha_cycling_generel.gpkg", None),
        (PROCESSED_DIR / "network" / "praha_streets_ruian.geojson", dirs["network"] / "praha_streets_ruian.gpkg", None),
        (PROCESSED_DIR / "boundaries" / "praha_mestske_casti.geojson", dirs["boundaries"] / "praha_mestske_casti.gpkg", None),
        (PROCESSED_DIR / "boundaries" / "praha_zsj.geojson", dirs["boundaries"] / "praha_zsj.gpkg", None),
        (PROCESSED_DIR / "boundaries" / "praha_buc.geojson", dirs["boundaries"] / "praha_buc.gpkg", None),
        (PROCESSED_DIR / "historical_paths" / "URK_CestyZanikle_l.shp", dirs["historical"] / "praha_vanished_paths.gpkg", 5514),
    ]
    for source, destination, expected_crs in geodata_sources:
        summary["geodata"][destination.stem] = write_geodata(source, destination, expected_crs=expected_crs)

    summary["tables"]["praha_population_city_parts"] = clean_population_workbook(
        PROCESSED_DIR / "population" / "praha_mestske_casti_population_sldb2021.xlsx",
        dirs["population"] / "praha_population_city_parts.csv",
    )
    summary["geodata"]["praha_mestske_casti_population"] = join_city_parts_with_population(
        dirs["boundaries"] / "praha_mestske_casti.gpkg",
        dirs["population"] / "praha_population_city_parts.csv",
        dirs["population"] / "praha_mestske_casti_population.gpkg",
    )
    summary["tables"]["gtfs"] = clean_gtfs_directory(PROCESSED_DIR / "gtfs", dirs["gtfs"])

    summary["assets"]["osm"] = copy_asset(
        PROCESSED_DIR / "network" / "praha-latest.osm.pbf",
        dirs["network"] / "praha-latest.osm.pbf",
    )
    summary["assets"]["terrain"] = copy_asset(
        PROCESSED_DIR / "terrain" / "praha_dmr5g_8m.tif",
        dirs["terrain"] / "praha_dmr5g_8m.tif",
    )

    readme = "\n".join(
        [
            "Cleaned data layout",
            "",
            "network/: cleaned network and cycling layers as GeoPackage, plus OSM PBF",
            "boundaries/: cleaned boundary layers as GeoPackage",
            "historical_paths/: cleaned vanished-path layer as GeoPackage in EPSG:4326",
            "population/: cleaned Prague city-part population CSV and join-ready city-part boundary GeoPackage",
            "gtfs/: GTFS text tables rewritten as normalized CSV",
            "terrain/: staged DMR5G raster",
            "metadata/: machine-readable cleaning summary",
        ]
    )
    (CLEANED_DIR / "README.txt").write_text(readme, encoding="utf-8")
    (dirs["metadata"] / "cleaning_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()