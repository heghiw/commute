from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "derived" / "analysis"
REPORT = ROOT / "reports" / "sensitivity_summary.json"


def rank_overlap(a: pd.Series, b: pd.Series, n: int = 10) -> int:
    return len(set(a.nlargest(n).index) & set(b.nlargest(n).index))


def main() -> None:
    benefits = pd.read_csv(ANALYSIS_DIR / "shortlist_origin_benefits.csv")
    zsj = gpd.read_file(ANALYSIS_DIR / "zsj_accessibility_baseline.gpkg")
    candidates = pd.read_csv(ANALYSIS_DIR / "shortlist_exact_evaluation.csv")[["candidate_id", "length_m"]]

    # Equal population per ZSJ.
    counts = zsj.groupby("city_part_name").kod.transform("count")
    zsj["population_equal_zsj"] = zsj.population_total / counts
    # Square-root area weighting.
    zsj["sqrt_area"] = np.sqrt(zsj.area_km2.clip(lower=0))
    denom = zsj.groupby("city_part_name").sqrt_area.transform("sum")
    zsj["population_sqrt_area"] = zsj.population_total * zsj.sqrt_area / denom
    weights = zsj[["kod", "population_est", "population_equal_zsj", "population_sqrt_area"]].rename(columns={"kod": "zsj_code"})
    benefits = benefits.drop(columns="population_area_weighted").merge(weights, on="zsj_code", how="left")

    scenarios = {"area": "population_est", "equal_zsj": "population_equal_zsj", "sqrt_area": "population_sqrt_area"}
    scores = candidates.copy()
    for name, column in scenarios.items():
        total = (benefits.minutes_saved * benefits[column]).groupby(benefits.candidate_id).sum()
        scores[f"benefit_{name}"] = scores.candidate_id.map(total).fillna(0)
        scores[f"benefit_per_m_{name}"] = scores[f"benefit_{name}"] / scores.length_m
        scores[f"rank_{name}"] = scores[f"benefit_per_m_{name}"].rank(ascending=False, method="min").astype(int)
    scores.to_csv(ANALYSIS_DIR / "population_sensitivity_scores.csv", index=False)

    correlations = scores[[f"benefit_per_m_{s}" for s in scenarios]].corr(method="spearman")
    overlaps = {f"area_vs_{s}_top10": rank_overlap(scores.set_index("candidate_id").benefit_per_m_area,
                                                    scores.set_index("candidate_id")[f"benefit_per_m_{s}"], 10)
                for s in ["equal_zsj", "sqrt_area"]}
    stable_top = scores[scores[[f"rank_{s}" for s in scenarios]].max(axis=1) <= 15].sort_values("rank_area")
    summary = {"method_version": "population-sensitivity-v1",
               "scenarios": {"area": "population proportional to ZSJ area",
                             "equal_zsj": "equal population per ZSJ within each city part",
                             "sqrt_area": "population proportional to square root of ZSJ area"},
               "spearman": correlations.round(3).to_dict(), "top10_overlap": overlaps,
               "stable_top15_candidates": stable_top.candidate_id.astype(int).tolist(),
               "interpretation": "Sensitivity isolates population-allocation uncertainty; network and impedance remain fixed."}
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
