#!/usr/bin/env python3
"""Script 8: generate risk alerts from the zone, spread and regime metrics.

Reads ``zone_stats``, ``spread_metrics`` and ``regime_profiles`` from
``artifacts/tables/`` and writes ``artifacts/alerts/alerts.{json,md}``.
The siting memo this script used to emit belonged to the retired startup
framing (docs/PROJECT_MAP.md §7); a report generator replaces it in a later
phase.
"""
from __future__ import annotations

import argparse

from baseload.pipeline_utils import ensure_dirs, load_config, write_json

from baseload.io import read_parquet


def classify(value: float, yellow: float, red: float, high_bad: bool = True) -> str:
    # Convert a raw metric value to an alert severity label using threshold bands.
    # - For high_bad=True, larger values are worse (upside risk).
    # - For high_bad=False, smaller values are worse (downside risk).
    # Threshold priority: red over yellow; green otherwise.
    if high_bad:
        if value >= red:
            return "red"
        if value >= yellow:
            return "yellow"
        return "green"
    if value <= red:
        return "red"
    if value <= yellow:
        return "yellow"
    return "green"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    th = cfg.get("thresholds", {})

    zs = read_parquet(paths["tables"] / "zone_stats.parquet")
    sp = read_parquet(paths["tables"] / "spread_metrics.parquet")
    rg = read_parquet(paths["tables"] / "regime_profiles.parquet")

    alerts = {}

    # Volatility compression: lower IQR is worse when high_bad=False.
    iqr_median = float(zs["iqr"].median())
    alerts["volatility_compression"] = {
        "metric": iqr_median,
        "severity": classify(iqr_median, th.get("iqr_yellow", 25), th.get("iqr_red", 15), high_bad=False),
        "action": "Typical intra-period price range is narrow; check whether volatility has compressed structurally.",
    }

    # Tail dependence risk: max tail ratio with high values being bad.
    tail = float(zs["tail_ratio"].max())
    alerts["tail_dependence"] = {
        "metric": tail,
        "severity": classify(tail, th.get("tail_yellow", 2.5), th.get("tail_red", 3.5), high_bad=True),
        "action": "Price distribution is dominated by rare spike periods; inspect the tail before relying on averages.",
    }

    # Congestion persistence risk: how many consecutive hours are separated by a spread.
    cong = float(sp["max_consecutive_separation_h"].max()) if len(sp) else 0.0
    alerts["congestion_persistence"] = {
        "metric": cong,
        "severity": classify(cong, th.get("congestion_yellow_h", 12), th.get("congestion_red_h", 24), high_bad=True),
        "action": "Long runs of zonal price separation; candidate interfaces for the where/when congestion analysis.",
    }

    # Oversupply risk: maximum of zone negative-price frequency and regime negative-price share.
    over = float(max(zs["negative_price_freq"].max(), rg["neg_price_share"].max()))
    alerts["oversupply_risk"] = {
        "metric": over,
        "severity": classify(over, th.get("oversupply_yellow", 0.05), th.get("oversupply_red", 0.1), high_bad=True),
        "action": "High share of negative-price periods; check for structural renewable oversupply.",
    }

    write_json(paths["alerts"] / "alerts.json", alerts)

    md_lines = ["# Alerts", ""]
    for name, item in alerts.items():
        md_lines.append(f"- **{name}**: {item['severity']} (metric={item['metric']:.3f}) — {item['action']}")
    (paths["alerts"] / "alerts.md").write_text("\n".join(md_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
