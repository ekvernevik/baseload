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
    write_prices(prices, paths)  # validates against PRICES_SCHEMA before writing

    if inputs.get("load"):
        load = load_zone_table(inputs["load"], paths["raw"], start, end, label="load")
        load.to_parquet(paths["processed"] / "load.parquet")

    if inputs.get("gen"):
        gen = load_zone_table(inputs["gen"], paths["raw"], start, end, label="gen")
        gen.to_parquet(paths["processed"] / "gen.parquet")


if __name__ == "__main__":
    main()
