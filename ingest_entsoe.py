#!/usr/bin/env python3
"""Script 1: ingest ENTSO-E CSV exports and store normalized parquet tables."""
from __future__ import annotations

import argparse
from pathlib import Path

from baseload.pipeline_utils import align_hourly, ensure_dirs, load_config, parse_csv_flexible, standardize_series

from baseload.io import write_prices, write_parquet


def load_zone_table(input_cfg: dict, raw_dir: Path, start: str, end: str, label: str):
    zone_map = {}
    for zone, rel_path in input_cfg.items():
        raw_path = raw_dir / rel_path
        df = parse_csv_flexible(raw_path)
        zone_map[zone] = standardize_series(df, zone=zone, value_name=label)
    return align_hourly(zone_map, start=start, end=end)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    start = cfg["date_range"]["start"]
    end = cfg["date_range"]["end"]
    inputs = cfg.get("inputs", {})

    if "prices" not in inputs:
        raise ValueError("Config must define inputs.prices with per-zone CSV paths")

    prices = load_zone_table(inputs["prices"], paths["raw"], start, end, label="price")

    # Early check: warn if any zone is more than 50% NaN (likely a date range mismatch)
    for zone in prices.columns:
        nan_pct = prices[zone].isna().mean()
        if nan_pct > 0.5:
            raise ValueError(
                f"Zone '{zone}' is {nan_pct:.0%} NaN after alignment — "
                f"CSV data probably does not cover the configured date range "
                f"({start} → {end})."
            )

    write_prices(prices, paths)  # validates against PRICES_SCHEMA before writing

    if inputs.get("load"):
        load = load_zone_table(inputs["load"], paths["raw"], start, end, label="load")
        for zone in load.columns:
            nan_pct = load[zone].isna().mean()
            if nan_pct > 0.5:
                raise ValueError(
                    f"Load zone '{zone}' is {nan_pct:.0%} NaN after alignment — "
                    f"CSV data probably does not cover the configured date range "
                    f"({start} → {end})."
                )
        write_parquet(load, paths["processed"] / "load.parquet", schema_name="load")

    if inputs.get("gen"):
        gen = load_zone_table(inputs["gen"], paths["raw"], start, end, label="gen")
        for zone in gen.columns:
            nan_pct = gen[zone].isna().mean()
            if nan_pct > 0.5:
                raise ValueError(
                    f"Gen zone '{zone}' is {nan_pct:.0%} NaN after alignment — "
                    f"CSV data probably does not cover the configured date range "
                    f"({start} → {end})."
                )
        write_parquet(gen, paths["processed"] / "gen.parquet", schema_name="gen")


if __name__ == "__main__":
    main()
