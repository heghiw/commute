from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import from_wkt, get_parts
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/derived/corrected_analysis"
REPORT = ROOT / "reports/corridor_connection_summary.json"
CRS = 5514
MAX_ENDPOINT_GAP_M = 20.0
MAX_PROXIMITY_GAP_M = 20.0
MEASURE_TOLERANCE_M = 1.0
PROXIMITY_SPACING_M = 15.0
SAME_ROAD_ACCESS_SPACING_M = 20.0
PARALLEL_ROAD_BUFFER_M = 10.0
PARALLEL_EDGE_OVERLAP_M = 30.0
PARALLEL_SCREEN_DISTANCE_M = 30.0
PARALLEL_SAMPLE_SPACING_M = 10.0
PARALLEL_COSINE_MIN = 0.866
MAJOR_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
                  "secondary", "secondary_link", "tertiary", "tertiary_link"}


def classify_intervention(records: list[dict], endpoint_valid: bool) -> tuple[str, str, int]:
    """Classify engineering scope without confusing feasibility with transport value."""
    unsafe = [r for r in records if r.get("active") and r.get("unsafe_major_crossing")]
    severe = [r for r in unsafe if r.get("osm_highway") in {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"}]
    if not endpoint_valid:
        return "C", "requires endpoint connection / land feasibility", len(unsafe)
    if severe or len(unsafe) >= 2:
        return "C", "major crossing or structure feasibility", len(unsafe)
    if unsafe:
        return "B", "protected crossing or junction intervention", len(unsafe)
    return "A", "direct connection / minor works", 0


def intersection_points(geometry) -> list[Point]:
    points = []
    for part in get_parts(geometry):
        if part.geom_type == "Point":
            points.append(part)
        elif part.geom_type == "LineString":
            points.extend([Point(part.coords[0]), Point(part.coords[-1])])
    return points


def deduplicate_access(records: list[dict]) -> list[dict]:
    records = sorted(records, key=lambda x: (x["measure_m"], x["connector_m"]))
    result = []
    for record in records:
        if result and abs(record["measure_m"] - result[-1]["measure_m"]) <= MEASURE_TOLERANCE_M:
            # Keep cycling status across coincident matches.
            result[-1]["cycle_existing"] = result[-1]["cycle_existing"] or record["cycle_existing"]
            unsafe = result[-1].get("unsafe_major_crossing", False) or record.get("unsafe_major_crossing", False)
            if record["connector_m"] < result[-1]["connector_m"]:
                cycle = result[-1]["cycle_existing"]
                result[-1] = record
                result[-1]["cycle_existing"] = cycle
            result[-1]["unsafe_major_crossing"] = unsafe and not result[-1]["cycle_existing"]
        else:
            result.append(record)
    return result


def deduplicate_same_road_access(records: list[dict]) -> list[dict]:
    """Keep one access point for nearby matches to the same road segment/junction."""
    preference = {"interior": 0, "endpoint": 1, "proximity": 2}
    ordered = sorted(records, key=lambda r: (float(r["connector_m"]), preference.get(r.get("kind"), 3)))
    kept = []
    for record in ordered:
        duplicate = False
        for prior in kept:
            same_edge = record.get("edge_id") == prior.get("edge_id")
            adjacent_same_road = (
                bool({record.get("edge_u"), record.get("edge_v")} & {prior.get("edge_u"), prior.get("edge_v")})
                and record.get("cycle_existing") == prior.get("cycle_existing")
                and Point(record.get("x", 0), record.get("y", 0)).distance(
                    Point(prior.get("x", 0), prior.get("y", 0))) <= SAME_ROAD_ACCESS_SPACING_M
            )
            if (same_edge or adjacent_same_road) and abs(record["measure_m"] - prior["measure_m"]) <= SAME_ROAD_ACCESS_SPACING_M:
                duplicate = True
                break
        if not duplicate:
            kept.append(record)
    return sorted(kept, key=lambda r: r["measure_m"])


def parallel_road_fraction(line, edges: gpd.GeoDataFrame, spatial) -> float:
    """Sample the share of a corridor following an existing road in the same direction."""
    if line.length <= 0:
        return 0.0
    measures = np.arange(min(5.0, line.length / 2), line.length, PARALLEL_SAMPLE_SPACING_M)
    matched = 0
    for measure in measures:
        point = line.interpolate(float(measure))
        before = line.interpolate(max(0.0, float(measure) - 5.0))
        after = line.interpolate(min(line.length, float(measure) + 5.0))
        direction = np.array([after.x - before.x, after.y - before.y])
        for edge_idx in spatial.query(point.buffer(PARALLEL_SCREEN_DISTANCE_M), predicate="intersects"):
            geometry = edges.iloc[int(edge_idx)].geometry
            projected = geometry.project(point)
            if point.distance(geometry.interpolate(projected)) > PARALLEL_SCREEN_DISTANCE_M:
                continue
            edge_before = geometry.interpolate(max(0.0, projected - 5.0))
            edge_after = geometry.interpolate(min(geometry.length, projected + 5.0))
            edge_direction = np.array([edge_after.x - edge_before.x, edge_after.y - edge_before.y])
            denominator = np.linalg.norm(direction) * np.linalg.norm(edge_direction)
            if denominator and abs(float(np.dot(direction, edge_direction) / denominator)) >= PARALLEL_COSINE_MIN:
                matched += 1
                break
    return float(matched / len(measures)) if len(measures) else 0.0


def add_proximity_access(records: list[dict], line, edges: gpd.GeoDataFrame, spatial) -> None:
    """Add short, explicit spurs where a corridor passes near a routable road.

    Exact crossings and endpoints remain preferred by deduplication because their
    connector length is zero or shorter. Spacing prevents a parallel street from
    generating hundreds of almost identical access points.
    """
    candidates = []
    for edge_idx in spatial.query(line.buffer(MAX_PROXIMITY_GAP_M), predicate="intersects"):
        edge = edges.iloc[int(edge_idx)]
        on_line, on_edge = nearest_points(line, edge.geometry)
        # Ignore nearby parallel streets as access spurs.
        measure = float(line.project(on_line))
        before = line.interpolate(max(0.0, measure - 5.0)); after = line.interpolate(min(line.length, measure + 5.0))
        edge_start = Point(edge.geometry.coords[0]); edge_end = Point(edge.geometry.coords[-1])
        corridor_vector = np.array([after.x - before.x, after.y - before.y])
        edge_vector = np.array([edge_end.x - edge_start.x, edge_end.y - edge_start.y])
        denominator = np.linalg.norm(corridor_vector) * np.linalg.norm(edge_vector)
        parallel = denominator > 0 and abs(float(np.dot(corridor_vector, edge_vector) / denominator)) >= 0.866
        if parallel and line.intersection(edge.geometry.buffer(MAX_PROXIMITY_GAP_M)).length > PARALLEL_EDGE_OVERLAP_M:
            continue
        gap = float(on_line.distance(on_edge))
        if gap <= MEASURE_TOLERANCE_M or gap > MAX_PROXIMITY_GAP_M:
            continue
        if measure <= MEASURE_TOLERANCE_M or measure >= line.length - MEASURE_TOLERANCE_M:
            continue
        candidates.append({
            "measure_m": measure, "edge_id": int(edge.edge_id),
            "edge_u": int(edge.u), "edge_v": int(edge.v),
            "edge_fraction": float(edge.geometry.project(on_edge) / edge.geometry.length),
            "connector_m": gap, "cycle_existing": bool(edge.cycle_existing),
            "osm_highway": edge.get("osm_highway"),
            "unsafe_major_crossing": bool(edge.get("osm_highway") in MAJOR_HIGHWAYS and not edge.cycle_existing),
            "active": True, "kind": "proximity",
            "x": on_edge.x, "y": on_edge.y,
            "corridor_x": on_line.x, "corridor_y": on_line.y,
        })
    # Keep one short spur per local interval.
    candidates.sort(key=lambda r: (r["connector_m"], r["measure_m"]))
    accepted = []
    for record in candidates:
        if all(abs(record["measure_m"] - prior["measure_m"]) > PROXIMITY_SPACING_M for prior in accepted):
            accepted.append(record)
    records.extend(accepted)


def main() -> None:
    edges = gpd.read_file(ROOT / "data/derived/corrected_graph/edges.gpkg").to_crs(CRS).reset_index(drop=True)
    corridors = gpd.read_file(OUT / "corrected_corridor_ranking.gpkg").to_crs(CRS)
    corridor_lines = gpd.GeoSeries.from_wkt(corridors.historical_wkt, crs=CRS)
    cycle_union = edges.loc[edges.cycle_existing, "geometry"].union_all()
    spatial = edges.sindex
    rows = []

    for corridor, line in zip(corridors.itertuples(), corridor_lines):
        records = []
        endpoint_gaps = []
        for measure, endpoint in ((0.0, Point(line.coords[0])), (float(line.length), Point(line.coords[-1]))):
            indices, distances = spatial.nearest(endpoint, return_all=False, return_distance=True)
            edge_idx = int(indices[1][0]); gap = float(distances[0]); edge = edges.iloc[edge_idx]
            projected = nearest_points(endpoint, edge.geometry)[1]
            edge_fraction = float(edge.geometry.project(projected) / edge.geometry.length)
            records.append({"measure_m": measure, "edge_id": int(edge.edge_id), "edge_u": int(edge.u), "edge_v": int(edge.v),
                            "edge_fraction": edge_fraction, "connector_m": gap,
                            "cycle_existing": bool(endpoint.distance(cycle_union) <= MAX_ENDPOINT_GAP_M),
                            "osm_highway": edge.get("osm_highway"),
                            "unsafe_major_crossing": bool(edge.get("osm_highway") in MAJOR_HIGHWAYS and not edge.cycle_existing),
                            "active": gap <= MAX_ENDPOINT_GAP_M,
                            "kind": "endpoint", "x": projected.x, "y": projected.y})
            endpoint_gaps.append(gap)

        for edge_idx in spatial.query(line, predicate="intersects"):
            edge = edges.iloc[int(edge_idx)]
            if bool(edge.get("grade_separated", False)):
                continue
            for point in intersection_points(line.intersection(edge.geometry)):
                measure = float(line.project(point))
                if measure <= MEASURE_TOLERANCE_M or measure >= line.length - MEASURE_TOLERANCE_M:
                    continue
                records.append({"measure_m": measure, "edge_id": int(edge.edge_id), "edge_u": int(edge.u), "edge_v": int(edge.v),
                                "edge_fraction": float(edge.geometry.project(point) / edge.geometry.length), "connector_m": 0.0,
                                "cycle_existing": bool(edge.cycle_existing), "osm_highway": edge.get("osm_highway"),
                                "unsafe_major_crossing": bool(edge.get("osm_highway") in MAJOR_HIGHWAYS and not edge.cycle_existing),
                                "kind": "interior", "x": point.x, "y": point.y})
                records[-1]["active"] = True
        add_proximity_access(records, line, edges, spatial)
        records = deduplicate_same_road_access(deduplicate_access(records))
        active_records = [r for r in records if r["active"]]
        near_indices = list(spatial.query(line.buffer(PARALLEL_ROAD_BUFFER_M), predicate="intersects"))
        nearby_coverage = edges.iloc[near_indices].geometry.buffer(PARALLEL_ROAD_BUFFER_M).union_all()
        near_road_fraction = min(1.0, float(line.intersection(nearby_coverage).length / line.length))
        parallel_fraction = parallel_road_fraction(line, edges, spatial)
        unsafe_major_crossing = any(r.get("unsafe_major_crossing", False) for r in active_records)
        cycle_measures = [r["measure_m"] for r in active_records if r["cycle_existing"]]
        endpoint_cycle = [line.coords[0] and Point(line.coords[0]).distance(cycle_union) <= MAX_ENDPOINT_GAP_M,
                          line.coords[-1] and Point(line.coords[-1]).distance(cycle_union) <= MAX_ENDPOINT_GAP_M]
        direct_closure = bool(endpoint_cycle[0] and endpoint_cycle[1])
        classification = "direct_cycle_closure" if direct_closure else ("cycle_extension" if cycle_measures else "street_network_link")
        endpoint_valid = max(endpoint_gaps) <= MAX_ENDPOINT_GAP_M
        # Interior access can connect roads despite isolated endpoints.
        network_connection_valid = len(active_records) >= 2
        tier, intervention_reason, crossing_count = classify_intervention(active_records, network_connection_valid)
        rows.append({"corridor_id": int(corridor.corridor_id), "endpoint_gap_u_m": endpoint_gaps[0],
                     "endpoint_gap_v_m": endpoint_gaps[1], "endpoint_edge_valid": endpoint_valid,
                     "access_points": len(records), "interior_access_points": sum(r["kind"] == "interior" for r in records),
                     "proximity_access_points": sum(r["kind"] == "proximity" for r in records),
                     "proximity_connector_m": sum(r["connector_m"] for r in records if r["kind"] == "proximity"),
                     "near_existing_road_fraction": near_road_fraction,
                     "parallel_existing_road_fraction": parallel_fraction,
                     "active_access_points": len(active_records), "unsafe_major_crossing": unsafe_major_crossing,
                     "routable_corridor": len(active_records) >= 2,
                     "intervention_tier": tier, "intervention_reason": intervention_reason,
                     "major_crossing_count": crossing_count,
                     "cycle_access_points": len(cycle_measures), "classification": classification,
                     "access_json": json.dumps(records, separators=(",", ":"))})

    connection = pd.DataFrame(rows)
    enriched = corridors.merge(connection, on="corridor_id", how="left")
    enriched.to_file(OUT / "edge_connected_corridors.gpkg", driver="GPKG")
    enriched.drop(columns="geometry").to_csv(OUT / "edge_connected_corridors.csv", index=False)
    summary = {"method": "edge-projected-intervention-classified-v2", "corridors": len(enriched),
               "endpoint_edge_valid": int(enriched.endpoint_edge_valid.sum()),
               "routable_corridors": int(enriched.routable_corridor.sum()),
               "with_interior_connections": int((enriched.interior_access_points > 0).sum()),
               "with_proximity_connections": int((enriched.proximity_access_points > 0).sum()),
               "proximity_access_points": int(enriched.proximity_access_points.sum()),
               "max_proximity_gap_m": MAX_PROXIMITY_GAP_M,
               "same_road_access_spacing_m": SAME_ROAD_ACCESS_SPACING_M,
               "parallel_road_buffer_m": PARALLEL_ROAD_BUFFER_M,
               "parallel_screen_distance_m": PARALLEL_SCREEN_DISTANCE_M,
               "major_crossing_candidates": int(enriched.unsafe_major_crossing.sum()),
               "intervention_tiers": {k: int(v) for k, v in enriched.intervention_tier.value_counts().to_dict().items()},
               "direct_cycle_closures": int((enriched.classification == "direct_cycle_closure").sum()),
               "cycle_extensions": int((enriched.classification == "cycle_extension").sum()),
               "street_network_links": int((enriched.classification == "street_network_link").sum()),
               "median_access_points": float(enriched.access_points.median()),
               "max_endpoint_gap_m": MAX_ENDPOINT_GAP_M}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
