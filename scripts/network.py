from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point


ROOT = Path(__file__).resolve().parents[1]
CLEANED_DIR = ROOT / "data" / "cleaned"
DERIVED_DIR = ROOT / "data" / "derived" / "graph"
REPORTS_DIR = ROOT / "reports"
GRAPH_CRS = 5514


def iter_lines(geometry):
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield geometry
        return
    if isinstance(geometry, MultiLineString):
        for part in geometry.geoms:
            if not part.is_empty:
                yield part


def round_coord(value: float) -> float:
    return round(float(value), 3)


def node_key(coord) -> tuple[float, float]:
    return (round_coord(coord[0]), round_coord(coord[1]))


def load_layer(relative_path: str, source_layer: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(CLEANED_DIR / relative_path).to_crs(GRAPH_CRS)
    gdf = gdf.copy()
    gdf["source_layer"] = source_layer
    return gdf


def main() -> None:
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    layer_specs = [
        {
            "path": "network/praha_streets_ruian.gpkg",
            "name": "streets",
            "label_column": "nazev",
            "extra_columns": ["kod", "ulice"],
        },
        {
            "path": "network/praha_cycling_routes.gpkg",
            "name": "cycling_routes",
            "label_column": "cislo_tras",
            "extra_columns": ["realizace", "dopr_stav", "jednosmerka", "ct"],
        },
        {
            "path": "network/praha_cycling_generel.gpkg",
            "name": "cycling_generel",
            "label_column": "nazev",
            "extra_columns": ["kategorie", "jmeno", "z_trasy", "na_trasu"],
        },
    ]

    graph = nx.MultiGraph()
    node_ids: dict[tuple[float, float], int] = {}
    node_records: list[dict[str, object]] = []
    edge_records: list[dict[str, object]] = []
    edge_id = 1

    def get_node_id(coord) -> int:
        key = node_key(coord)
        if key not in node_ids:
            node_id = len(node_ids) + 1
            node_ids[key] = node_id
            node_records.append(
                {
                    "node_id": node_id,
                    "x_5514": key[0],
                    "y_5514": key[1],
                    "geometry": Point(key[0], key[1]),
                }
            )
        return node_ids[key]

    layer_summaries = {}

    for spec in layer_specs:
        gdf = load_layer(spec["path"], spec["name"])
        segment_count = 0
        layer_length_m = 0.0

        for _, row in gdf.iterrows():
            geometry = row.geometry
            for line in iter_lines(geometry):
                coords = list(line.coords)
                if len(coords) < 2:
                    continue

                for start, end in zip(coords[:-1], coords[1:]):
                    segment = LineString([start, end])
                    length_m = float(segment.length)
                    if length_m <= 0:
                        continue

                    u = get_node_id(start)
                    v = get_node_id(end)
                    attributes = {
                        "edge_id": edge_id,
                        "u": u,
                        "v": v,
                        "source_layer": spec["name"],
                        "source_objectid": row.get("objectid"),
                        "source_globalid": row.get("globalid"),
                        "name": row.get(spec["label_column"]),
                        "length_m": length_m,
                        "geometry": segment,
                    }
                    for column in spec["extra_columns"]:
                        attributes[column] = row.get(column)

                    graph.add_edge(u, v, key=edge_id, **attributes)
                    edge_records.append(attributes)
                    edge_id += 1
                    segment_count += 1
                    layer_length_m += length_m

        layer_summaries[spec["name"]] = {
            "source": spec["path"],
            "segments": segment_count,
            "total_length_km": round(layer_length_m / 1000.0, 2),
        }

    nodes_gdf = gpd.GeoDataFrame(node_records, geometry="geometry", crs=GRAPH_CRS)
    edges_gdf = gpd.GeoDataFrame(edge_records, geometry="geometry", crs=GRAPH_CRS)

    nodes_wgs84 = nodes_gdf.to_crs(4326)
    edges_wgs84 = edges_gdf.to_crs(4326)

    nodes_path = DERIVED_DIR / "prague_graph_nodes.gpkg"
    edges_path = DERIVED_DIR / "prague_graph_edges.gpkg"
    adjacency_path = DERIVED_DIR / "prague_graph_edge_table.csv"

    nodes_wgs84.to_file(nodes_path, driver="GPKG")
    edges_wgs84.to_file(edges_path, driver="GPKG")

    edge_table = pd.DataFrame(edges_gdf.drop(columns="geometry"))
    edge_table.to_csv(adjacency_path, index=False)

    connected_components = sorted((len(component) for component in nx.connected_components(graph)), reverse=True)
    degree_series = pd.Series(dict(graph.degree()))

    summary = {
        "graph": {
            "type": "networkx_multigraph",
            "crs_for_construction": f"EPSG:{GRAPH_CRS}",
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "connected_components": len(connected_components),
            "largest_component_nodes": connected_components[0] if connected_components else 0,
            "mean_node_degree": round(float(degree_series.mean()), 3) if not degree_series.empty else 0.0,
            "median_node_degree": round(float(degree_series.median()), 3) if not degree_series.empty else 0.0,
        },
        "layers": layer_summaries,
        "outputs": {
            "nodes": str(nodes_path.relative_to(ROOT)),
            "edges": str(edges_path.relative_to(ROOT)),
            "edge_table": str(adjacency_path.relative_to(ROOT)),
        },
    }

    report_path = REPORTS_DIR / "graph_summary.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Prague graph build summary")
    print(f"Nodes: {summary['graph']['node_count']}")
    print(f"Edges: {summary['graph']['edge_count']}")
    print(f"Connected components: {summary['graph']['connected_components']}")
    print(f"Largest component nodes: {summary['graph']['largest_component_nodes']}")
    print(f"Edge outputs: {summary['outputs']['edges']}")
    print(f"Graph summary written to: {report_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()