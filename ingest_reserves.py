#!/usr/bin/env python3
"""Ingest Nordic reserve-market CSV exports (mFRR EAM, PICASSO aFRR).

Both markets report, per zone and settlement period, a capacity-reservation
stage and an activation-energy stage, each with a price and a volume, split
by direction (up/down) — the same long-format per-zone CSV shape ENTSO-E's
Transparency Platform "Balancing" domain publishes for day-ahead prices/load/
actgen/transmission, so this reuses the same parsing idioms as
``ingest_entsoe.py``.

Outputs (hourly UTC, one file per artifact in ``cfg["inputs"]``):
  data/processed/mfrr_capacity.parquet
  data/processed/mfrr_activation.parquet
  data/processed/afrr_capacity.parquet
  data/processed/afrr_activation.parquet
Columns per zone: "{zone}_up_price", "{zone}_up_volume",
"{zone}_down_price", "{zone}_down_volume".

Run:
  python ingest_reserves.py --config configs/demo.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config, parse_csv_flexible
from baseload.schemas import build_reserve_schema
from baseload.validators import validate_dataframe

_ARTIFACTS = ["mfrr_capacity", "mfrr_activation", "afrr_capacity", "afrr_activation"]


def load_reserve_table(input_cfg: dict, raw_dir: Path, start: str, end: str) -> pd.DataFrame:
    """Read per-zone reserve CSVs and pivot to a flat wide hourly table.

    Each CSV is long-format: one row per hour per direction (Up/Down), with
    a price and a volume column. `input_cfg` maps zone names to relative
    CSV paths under `raw_dir` (same convention as `ingest_entsoe.py`).

    Returns a DataFrame with columns "{zone}_{direction}_price" and
    "{zone}_{direction}_volume" for every zone in `input_cfg`.
    """
    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    all_frames = []

    for zone, rel_path in input_cfg.items():
        df = parse_csv_flexible(raw_dir / rel_path)

        time_col = next(c for c in df.columns if "mtu" in c.lower())
        dir_col = next(c for c in df.columns if "direction" in c.lower())
        price_col = next(c for c in df.columns if "price" in c.lower())
        volume_col = next(c for c in df.columns if "volume" in c.lower())

        df = df[[time_col, dir_col, price_col, volume_col]].copy()
        df["_time"] = pd.to_datetime(
            df[time_col].astype(str).str.split(" - ").str[0].str.strip(),
            dayfirst=True,
            errors="coerce",
        ).dt.tz_localize("UTC")
        df["_direction"] = df[dir_col].astype(str).str.strip().str.lower()
        df["_price"] = pd.to_numeric(df[price_col], errors="coerce")
        df["_volume"] = pd.to_numeric(df[volume_col], errors="coerce")
        df["_zone"] = zone

        df = df.dropna(subset=["_time"])
        df = df[df["_direction"].isin(["up", "down"])]

        all_frames.append(df[["_time", "_zone", "_direction", "_price", "_volume"]])

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["_time", "_zone", "_direction"])

    price = combined.pivot_table(
        index="_time", columns=["_zone", "_direction"], values="_price", aggfunc="mean"
    )
    volume = combined.pivot_table(
        index="_time", columns=["_zone", "_direction"], values="_volume", aggfunc="mean"
    )

    # Emit all four columns for every configured zone, even when a direction
    # never appears in the data (e.g. a zone that only procures upward
    # capacity) — the schema requires the columns to exist; absent trades are
    # all-NaN, not missing columns.
    out = pd.DataFrame(index=idx)
    for zone in input_cfg:
        for direction in ("up", "down"):
            out[f"{zone}_{direction}_price"] = price[(zone, direction)].reindex(idx) if (zone, direction) in price.columns else float("nan")
            out[f"{zone}_{direction}_volume"] = volume[(zone, direction)].reindex(idx) if (zone, direction) in volume.columns else float("nan")

    out.index.name = "time"
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    dr = cfg.get("date_range", {})
    start, end = dr["start"], dr["end"]
    inputs = cfg.get("inputs", {})

    ran_any = False
    for artifact in _ARTIFACTS:
        input_cfg = inputs.get(artifact)
        if not input_cfg:
            continue
        ran_any = True
        table = load_reserve_table(input_cfg, paths["raw"], start, end)

        # Validate against a schema built for the zones actually configured for
        # this artifact — unlike prices/load, partial zone coverage is the norm
        # for reserve markets (e.g. aFRR is only live in Finland today), so this
        # does not go through SCHEMA_REGISTRY's fixed NO1-NO5 default.
        schema = build_reserve_schema(artifact, sorted(input_cfg))
        validate_dataframe(table, schema, raise_on_error=True)

        out_path = paths["processed"] / f"{artifact}.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(out_path, index=True)
        print(f"Saved {artifact} -> {out_path}  shape={table.shape}")

    if not ran_any:
        print(
            "No reserve-market inputs configured — skipping.\n"
            "  Set inputs.mfrr_capacity / mfrr_activation / afrr_capacity / afrr_activation "
            "(zone -> CSV path) in your config to enable."
        )


if __name__ == "__main__":
    main()
