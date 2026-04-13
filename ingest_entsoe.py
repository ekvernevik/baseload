#!/usr/bin/env python3
"""Script 1: ingest ENTSO-E CSV exports and store normalized parquet tables."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from baseload.pipeline_utils import align_hourly, ensure_dirs, load_config, parse_csv_flexible, standardize_series

from baseload.io import write_prices, write_parquet, write_gen, write_transmission, write_external_balance

# Generation types that carry meaningful volume in the Norwegian grid.
_RELEVANT_GEN_TYPES = {
    "Hydro Water Reservoir",
    "Hydro Run-of-river and pondage",
    "Hydro Pumped Storage",
    "Wind Onshore",
    "Fossil Gas",
    "Waste",
    "Other renewable",
    "Solar",
    "Wind Offshore",
}

_NO_ZONES = {"NO1", "NO2", "NO3", "NO4", "NO5"}


def load_zone_table(input_cfg: dict, raw_dir: Path, start: str, end: str, label: str):
    # Read multiple per-zone CSV exports and normalize to a uniform hourly index.
    # `input_cfg` maps zone names to relative CSV paths under raw_dir.
    zone_map = {}
    for zone, rel_path in input_cfg.items():
        raw_path = raw_dir / rel_path
        df = parse_csv_flexible(raw_path)
        zone_map[zone] = standardize_series(df, zone=zone, value_name=label)

    # Align all series to the desired global start/end window and hourly frequency.
    return align_hourly(zone_map, start=start, end=end)


def load_load_table(input_cfg: dict, load_dir: Path, start: str, end: str):
    # Same structure as prices but CSVs contain both actual and forecast columns.
    # Drop the forecast column so standardize_series picks actual load unambiguously.
    zone_map = {}
    for zone, rel_path in input_cfg.items():
        df = parse_csv_flexible(load_dir / rel_path)
        forecast_cols = [c for c in df.columns if "forecast" in c.lower()]
        df = df.drop(columns=forecast_cols)
        zone_map[zone] = standardize_series(df, zone=zone, value_name="load")
    return align_hourly(zone_map, start=start, end=end)


def _parse_mtu(series: pd.Series) -> pd.DatetimeIndex:
    # ENTSO-E MTU column: "01/01/2025 00:00:00 - 01/01/2025 01:00:00" — take the start.
    return pd.to_datetime(
        series.astype(str).str.split(" - ").str[0].str.strip(),
        dayfirst=True,
        errors="coerce",
    ).dt.tz_localize("UTC")


def load_actgen_table(input_cfg: dict, actgen_dir: Path, start: str, end: str):
    # Long-format CSVs: one row per hour per production type per zone.
    # Output: DataFrame with MultiIndex columns (zone, production_type).
    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    all_frames = []

    for zone, rel_path in input_cfg.items():
        df = parse_csv_flexible(actgen_dir / rel_path)

        time_col = next(c for c in df.columns if "mtu" in c.lower())
        type_col = next(c for c in df.columns if "production type" in c.lower())
        gen_col = next(c for c in df.columns if "generation" in c.lower())

        df = df[[time_col, type_col, gen_col]].copy()
        df["_time"] = _parse_mtu(df[time_col])
        df["_gen"] = pd.to_numeric(df[gen_col], errors="coerce")
        df["_type"] = df[type_col]
        df["_zone"] = zone

        all_frames.append(df[["_time", "_zone", "_type", "_gen"]])

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined[combined["_type"].isin(_RELEVANT_GEN_TYPES)]
    combined = combined.dropna(subset=["_time", "_gen"])

    pivoted = combined.pivot_table(
        index="_time", columns=["_zone", "_type"], values="_gen", aggfunc="mean"
    )
    return pivoted.reindex(idx)


def _extract_no_zone(area: str) -> str | None:
    # "BZN|NO1" -> "NO1"; returns None for external zones (SE3, DK1, etc.)
    m = re.search(r"NO[1-5]$", str(area))
    return m.group() if m else None


def load_transmission_table(input_cfg: dict, transmission_dir: Path, start: str, end: str):
    # Long-format CSVs: one row per direction per interconnect per hour.
    # Each pair (e.g. NO1-NO2) appears in both zone files — deduplicate before computing
    # net flow.
    #
    # Returns two DataFrames:
    #   internal  — one column per internal NO zone pair, net flow toward higher-numbered zone
    #   external  — one column per NO zone, net external import (positive = importing)
    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    all_frames = []

    for zone, rel_path in input_cfg.items():
        df = parse_csv_flexible(transmission_dir / rel_path)

        time_col = next(c for c in df.columns if "mtu" in c.lower())
        out_col = next(c for c in df.columns if "out" in c.lower() and "area" in c.lower())
        in_col = next(c for c in df.columns if "in" in c.lower() and "area" in c.lower())
        flow_col = next(c for c in df.columns if "flow" in c.lower())

        df = df[[time_col, out_col, in_col, flow_col]].copy()
        df["_time"] = _parse_mtu(df[time_col])
        df["_flow"] = pd.to_numeric(df[flow_col], errors="coerce")
        df["_from"] = df[out_col].apply(_extract_no_zone)
        df["_to"] = df[in_col].apply(_extract_no_zone)

        all_frames.append(df[["_time", "_from", "_to", "_flow"]])

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.dropna(subset=["_time", "_flow"])

    # --- Internal NO-NO flows ------------------------------------------------
    internal = combined[
        combined["_from"].isin(_NO_ZONES) & combined["_to"].isin(_NO_ZONES)
    ].copy()
    internal = internal.drop_duplicates(subset=["_time", "_from", "_to"])
    internal["_pair"] = internal.apply(
        lambda r: f"{min(r['_from'], r['_to'])}-{max(r['_from'], r['_to'])}", axis=1
    )
    internal["_net"] = internal.apply(
        lambda r: r["_flow"] if r["_from"] < r["_to"] else -r["_flow"], axis=1
    )
    net_internal = internal.groupby(["_time", "_pair"])["_net"].sum()
    internal_df = net_internal.unstack("_pair").reindex(idx)
    internal_df.index.name = "time"

    # --- External balance per NO zone ----------------------------------------
    # Rows where exactly one side is a NO zone and the other is external (NaN).
    # Net import into a zone = sum of flows arriving - sum of flows leaving.
    from_is_no = combined["_from"].isin(_NO_ZONES)
    to_is_no = combined["_to"].isin(_NO_ZONES)
    ext = combined[from_is_no ^ to_is_no].copy()
    # External rows appear in exactly one zone file, so no deduplication needed here.

    # For each row: which NO zone is involved, and is the flow arriving (+) or leaving (-)?
    def _ext_zone_and_sign(row):
        if row["_from"] in _NO_ZONES:
            return row["_from"], -row["_flow"]   # leaving NO zone = negative import
        else:
            return row["_to"], row["_flow"]       # arriving at NO zone = positive import

    parsed = [_ext_zone_and_sign(r) for _, r in ext.iterrows()]
    ext = ext.copy()
    ext["_zone"] = [p[0] for p in parsed]
    ext["_import"] = [p[1] for p in parsed]
    net_ext = ext.groupby(["_time", "_zone"])["_import"].sum()
    external_df = net_ext.unstack("_zone").reindex(idx)
    external_df.index.name = "time"
    # Ensure all 5 zones are present even if some have no external connections.
    for z in _NO_ZONES:
        if z not in external_df.columns:
            external_df[z] = 0.0

    return internal_df, external_df


def _validate_date_range(cfg: dict) -> tuple[str, str]:
    """Validate date_range config early with clear error messages."""
    dr = cfg.get("date_range", {})
    start_str = dr.get("start")
    end_str = dr.get("end")

    if not start_str or not end_str:
        raise ValueError(
            "Config must define date_range.start and date_range.end.\n"
            "Example:\n  date_range:\n    start: '2025-01-01T00:00:00Z'\n    end: '2025-12-31T23:00:00Z'"
        )

    try:
        start_ts = pd.Timestamp(start_str)
    except Exception:
        raise ValueError(f"date_range.start is not a valid timestamp: '{start_str}'")

    try:
        end_ts = pd.Timestamp(end_str)
    except Exception:
        raise ValueError(f"date_range.end is not a valid timestamp: '{end_str}'")

    if start_ts >= end_ts:
        raise ValueError(
            f"date_range.start ({start_str}) must be before date_range.end ({end_str})."
        )

    days = (end_ts - start_ts).days
    if days > 1825:
        print(f"  WARNING: date_range spans {days} days (~{days//365}y). Large ranges may be slow.")

    return start_str, end_str


def _check_dst_gaps(df: pd.DataFrame, name: str) -> None:
    """Warn if a DataFrame has missing or duplicate hours at DST boundaries.

    ENTSO-E CET/CEST data often has off-by-one issues at clock-change hours:
      - Spring forward (e.g. 2025-03-30 02:00 CET → 03:00 CEST): 1 missing hour
      - Fall back  (e.g. 2025-10-26 03:00 CEST → 02:00 CET): 1 duplicate hour
    """
    if df.empty or not hasattr(df.index, "freq"):
        return

    expected = pd.date_range(df.index.min(), df.index.max(), freq="h", tz="UTC")
    missing = expected.difference(df.index)
    extra = df.index.difference(expected)

    if len(missing) > 0:
        print(f"  WARNING [{name}]: {len(missing)} missing hour(s) — possible DST gap.")
        if len(missing) <= 5:
            for ts in missing:
                print(f"    {ts}")
    if len(extra) > 0:
        print(f"  WARNING [{name}]: {len(extra)} unexpected hour(s) — possible DST duplicate.")
        if len(extra) <= 5:
            for ts in extra:
                print(f"    {ts}")


def _save_parquet(df: pd.DataFrame, path: "Path", name: str) -> None:
    """Write parquet atomically (temp file + rename) to avoid partial writes on crash."""
    tmp = path.with_suffix(".parquet.tmp")
    try:
        df.to_parquet(tmp)
        tmp.rename(path)
    except Exception as exc:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"Failed to save {name} to {path}: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    start, end = _validate_date_range(cfg)
    inputs = cfg.get("inputs", {})

    if "prices" not in inputs:
        raise ValueError("Config must define inputs.prices with per-zone CSV paths")

    # Import and normalize price data, then save as parquet for downstream scripts.
    prices = load_zone_table(inputs["prices"], paths["raw"], start, end, label="price")

    # Early check: fail fast if data does not cover the configured date range
    for zone in prices.columns:
        nan_pct = prices[zone].isna().mean()
        if nan_pct > 0.5:
            raise ValueError(
                f"Zone '{zone}' is {nan_pct:.0%} NaN after alignment — "
                f"CSV data probably does not cover the configured date range "
                f"({start} → {end})."
            )

    _check_dst_gaps(prices, "prices")
    write_prices(prices, paths)  # validates against PRICES_SCHEMA before writing
    print(f"Saved prices -> {paths['processed'] / 'prices.parquet'}  shape={prices.shape}")

    if inputs.get("load"):
        load = load_load_table(inputs["load"], paths["raw"], start, end)
        for zone in load.columns:
            nan_pct = load[zone].isna().mean()
            if nan_pct > 0.5:
                raise ValueError(
                    f"Load zone '{zone}' is {nan_pct:.0%} NaN after alignment — "
                    f"CSV data probably does not cover the configured date range "
                    f"({start} → {end})."
                )
        _check_dst_gaps(load, "load")
        write_parquet(load, paths["processed"] / "load.parquet", schema_name="load")
        print(f"Saved load -> {paths['processed'] / 'load.parquet'}  shape={load.shape}")

    if inputs.get("actgen"):
        actgen = load_actgen_table(inputs["actgen"], paths["raw"], start, end)
        write_gen(actgen, paths)  # validates against GEN_SCHEMA before writing
        print(f"Saved actgen -> {paths['processed'] / 'actgen.parquet'}  shape={actgen.shape}")

    if inputs.get("transmission"):
        transmission, external_balance = load_transmission_table(
            inputs["transmission"], paths["raw"], start, end
        )
        _check_dst_gaps(transmission, "transmission")
        write_transmission(transmission, paths)          # validates against TRANSMISSION_INTERNAL_SCHEMA
        write_external_balance(external_balance, paths)  # validates against EXTERNAL_BALANCE_SCHEMA
        print(f"Saved transmission -> shape={transmission.shape}")
        print(f"Saved external_balance -> shape={external_balance.shape}")


if __name__ == "__main__":
    main()
