#!/usr/bin/env python3
"""Script 1b: ingest Nordic reserve-market exports (mFRR EAM, aFRR, FCR-D).

Reads per-zone CSV exports with the ENTSO-E/Nordic balancing format

    MTU, Direction, Price [EUR/MWh], Volume [MW]

(mFRR EAM live Mar 2025; PICASSO aFRR live for FI Mar 2025) and lands them as
schema-validated parquet tables:

    data/processed/reserves_mfrr_eam.parquet
    data/processed/reserves_afrr.parquet
    data/processed/reserves_fcr_d.parquet

Output layout: wide DataFrame with MultiIndex columns (zone, field) where
field ∈ {up_price, down_price, up_volume, down_volume}. Files without a
Direction column (e.g. symmetric FCR-D capacity exports) are treated as "up".

Config:

    inputs:
      reserves:
        mfrr_eam:
          NO1: reserves/no1_mfrr_eam.csv
        afrr:
          NO1: reserves/no1_afrr.csv
        fcr_d: {}

Gaps are expected — reserve series only exist for MTUs with activation/auction
results — so continuity is not enforced, but prices/volumes are bound-checked.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from baseload.io import write_reserves
from baseload.pipeline_utils import ensure_dirs, load_config, parse_csv_flexible, resolution_freq
from baseload.schemas import RESERVE_MARKETS

from ingest_entsoe import _parse_mtu, _validate_date_range


def _find_col(df: pd.DataFrame, needle: str) -> str | None:
    return next((c for c in df.columns if needle in c.lower()), None)


def load_reserve_zone(csv_path: Path, freq: str) -> dict[str, pd.Series]:
    """Parse one zone's reserve CSV into {field: series} at target *freq*.

    Fields are up_price/down_price [EUR/MWh or EUR/MW] and up_volume/down_volume
    [MW]. Direction labels are matched case-insensitively; a missing Direction
    column means a symmetric/one-direction product and maps to "up".
    """
    df = parse_csv_flexible(csv_path)

    time_col = _find_col(df, "mtu") or df.columns[0]
    dir_col = _find_col(df, "direction")
    price_col = _find_col(df, "price")
    volume_col = _find_col(df, "volume")
    if price_col is None and volume_col is None:
        raise ValueError(f"No price or volume column found in {csv_path}")

    if dir_col is not None:
        directions = df[dir_col].astype(str).str.strip().str.lower()
    else:
        directions = pd.Series("up", index=df.index)

    fields: dict[str, pd.Series] = {}
    for direction in ("up", "down"):
        mask = (directions == direction).values
        if not mask.any():
            continue
        sub = df.loc[mask]
        sub_idx = _parse_mtu(sub[time_col])
        for col, field in ((price_col, "price"), (volume_col, "volume")):
            if col is None:
                continue
            ser = pd.Series(
                pd.to_numeric(sub[col], errors="coerce").values, index=sub_idx
            )
            ser = ser[~ser.index.isna()].sort_index().groupby(level=0).mean()
            # Reserve results are step values over their MTU: aggregate finer
            # sources by mean, never fill gaps (a missing MTU = no activation).
            fields[f"{direction}_{field}"] = ser.resample(freq).mean()

    return fields


def load_reserves_table(
    input_cfg: dict, raw_dir: Path, start: str, end: str, freq: str = "15min"
) -> pd.DataFrame:
    """Combine per-zone reserve CSVs into one (zone, field) MultiIndex table."""
    idx = pd.date_range(start=start, end=end, freq=freq, tz="UTC")
    pieces: dict[tuple[str, str], pd.Series] = {}

    for zone, rel_path in input_cfg.items():
        fields = load_reserve_zone(raw_dir / rel_path, freq=freq)
        if not fields:
            raise ValueError(f"No reserve series parsed for zone {zone} from {rel_path}")
        for field, ser in fields.items():
            pieces[(zone, field)] = ser.reindex(idx)

    table = pd.DataFrame(pieces, index=idx)
    table.columns = pd.MultiIndex.from_tuples(table.columns, names=["_zone", "_field"])
    return table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    start, end = _validate_date_range(cfg)
    freq = resolution_freq(cfg)

    reserves_cfg = (cfg.get("inputs") or {}).get("reserves") or {}
    if not reserves_cfg:
        raise ValueError(
            "Config must define inputs.reserves with per-market, per-zone CSV paths "
            f"(markets: {RESERVE_MARKETS})."
        )

    for market in RESERVE_MARKETS:
        zone_cfg = reserves_cfg.get(market) or {}
        if not zone_cfg:
            continue
        table = load_reserves_table(zone_cfg, paths["raw"], start, end, freq=freq)
        write_reserves(table, paths, market, freq=freq)  # validates against reserves_<market> schema
        n_series = table.notna().any().sum()
        print(
            f"Saved {market} -> {paths['processed'] / f'reserves_{market}.parquet'}  "
            f"shape={table.shape}  non-empty series={n_series}"
        )


if __name__ == "__main__":
    main()
