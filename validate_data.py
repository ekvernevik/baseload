#!/usr/bin/env python3
"""Script 2: validate processed data quality and write provenance metadata."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config, sha256_file, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices_path = paths["processed"] / "prices.parquet"
    if not prices_path.exists():
        raise FileNotFoundError("Missing processed prices parquet. Run ingest_entsoe.py first.")

    prices = pd.read_parquet(prices_path)
    issues = []
    expected_idx = pd.date_range(prices.index.min(), prices.index.max(), freq="h", tz="UTC")
    missing_hours = expected_idx.difference(prices.index)
    if len(missing_hours):
        issues.append(f"Missing hours: {len(missing_hours)}")
    if prices.index.has_duplicates:
        issues.append("Duplicate timestamps found")
    if str(prices.index.tz) != "UTC":
        issues.append(f"Index timezone is not UTC: {prices.index.tz}")

    # 6σ threshold rather than 3σ: Nordic prices have genuine fat tails
    # (hydro scarcity spikes, interconnector trips). 3σ would flag real events
    # as outliers. 6σ catches unit errors or data corruption while ignoring
    # legitimate extreme prices.
    outliers = ((prices - prices.mean()) / prices.std(ddof=0)).abs() > 6
    outlier_count = int(outliers.sum().sum())
    issues.append(f"Outliers (>6σ): {outlier_count}")

    val_md = paths["metadata"] / "validation.md"
    lines = ["# Validation Report", "", f"Rows: {len(prices)}", f"Zones: {len(prices.columns)}", ""]
    if issues:
        lines.append("## Findings")
        lines.extend([f"- {item}" for item in issues])
    else:
        lines.append("No issues found.")
    val_md.write_text("\n".join(lines), encoding="utf-8")

    # SHA256 checksums on input parquets: if downstream results look wrong,
    # provenance.json confirms whether the data changed between pipeline runs.
    prov = {
        "config": {
            "zones": cfg.get("zones", []),
            "date_range": cfg.get("date_range", {}),
        },
        "files": {
            str(prices_path): sha256_file(prices_path),
        },
    }
    for opt in ["load.parquet", "gen.parquet"]:
        p = paths["processed"] / opt
        if p.exists():
            prov["files"][str(p)] = sha256_file(p)
    write_json(paths["metadata"] / "provenance.json", prov)


if __name__ == "__main__":
    main()
