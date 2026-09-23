from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[1]
CRS = 5514
MAX_SNAP_M = 200.0


def snap_nodes(points: gpd.GeoSeries, tree: cKDTree, node_ids: np.ndarray,
               max_distance_m: float = MAX_SNAP_M) -> tuple[set[int], int]:
    xy = np.column_stack([points.x, points.y])
    distance, index = tree.query(xy)
    accepted = distance <= max_distance_m
    return set(node_ids[index[accepted]].astype(int)), int(accepted.sum())


def load_destination_points() -> dict[str, gpd.GeoDataFrame]:
    metro = gpd.read_file(ROOT / "data/processed/network/praha_metro_entrances.geojson").to_crs(CRS)
    metro = metro[["uzel_nazev", "geometry"]].rename(columns={"uzel_nazev": "name"})

    routes = pd.read_csv(ROOT / "data/cleaned/gtfs/routes.csv", low_memory=False)
    route_stops = pd.read_csv(ROOT / "data/cleaned/gtfs/route_stops.csv", low_memory=False)
    stops = pd.read_csv(ROOT / "data/cleaned/gtfs/stops.csv", low_memory=False)
    train_routes = set(routes.loc[routes.route_type.astype(str).eq("2"), "route_id"])
    train_stop_ids = set(route_stops.loc[route_stops.route_id.isin(train_routes), "stop_id"])
    train_stops = stops.loc[stops.stop_id.isin(train_stop_ids)].dropna(subset=["stop_lon", "stop_lat"])
    train_stops = train_stops.drop_duplicates("stop_id").reset_index(drop=True)
    train_points = gpd.GeoSeries(
        gpd.points_from_xy(train_stops.stop_lon, train_stops.stop_lat), crs=4326
    ).to_crs(CRS)
    city = gpd.read_file(ROOT / "data/cleaned/boundaries/praha_mestske_casti.gpkg").to_crs(CRS).geometry.union_all()
    inside = train_points.within(city)
    train = gpd.GeoDataFrame(
        {"name": train_stops.loc[inside, "stop_name"].to_numpy()},
        geometry=train_points.loc[inside].reset_index(drop=True), crs=CRS,
    )
    return {"metro": metro, "train": train}


def load_destinations(tree: cKDTree, node_ids: np.ndarray) -> tuple[dict[str, set[int]], dict]:
    points = load_destination_points()
    metro_nodes, metro_snapped = snap_nodes(points["metro"].geometry, tree, node_ids)
    train_nodes, train_snapped = snap_nodes(points["train"].geometry, tree, node_ids)

    summary = {
        "method": "metro entrances plus train-served GTFS stops in Prague",
        "max_street_snap_m": MAX_SNAP_M,
        "metro_entrances": int(len(points["metro"])),
        "metro_entrances_snapped": metro_snapped,
        "metro_destination_nodes": len(metro_nodes),
        "train_stops_in_prague": int(len(points["train"])),
        "train_stops_snapped": train_snapped,
        "train_destination_nodes": len(train_nodes),
        "train_gtfs_route_type": "2",
    }
    return {"metro": metro_nodes, "train": train_nodes}, summary
