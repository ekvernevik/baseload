#!/usr/bin/env python3
"""Fetch supplemental hydro data: reservoir fill levels and inflow.

Outputs (hourly, UTC-indexed, saved to processed_dir):
  hydro_reservoir.parquet  — columns: NO1..NO5, values: % fill  [requires Nord Pool CSV or API key]
  hydro_inflow.parquet     — columns: <station_id>, values: m³/s [requires NVE API key]

Run from the baseload/ directory:
  python ingest_supplemental.py --config configs/demo.yaml

Both outputs are optional — the congestion model uses whatever is present.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd
import requests

from baseload.pipeline_utils import ensure_dirs, load_config


def _get_with_retry(url: str, params: dict, headers: dict | None = None, timeout: int = 30, max_retries: int = 3) -> requests.Response:
    """GET with exponential backoff on transient errors (5xx, connection issues)."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp
            print(f"  Server error {resp.status_code} on attempt {attempt + 1}/{max_retries}. Retrying in {delay:.0f}s...")
        except requests.ConnectionError as exc:
            print(f"  Connection error on attempt {attempt + 1}/{max_retries}: {exc}. Retrying in {delay:.0f}s...")
        except requests.Timeout:
            print(f"  Timeout on attempt {attempt + 1}/{max_retries}. Retrying in {delay:.0f}s...")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"Failed after {max_retries} attempts: GET {url}")


# ---------------------------------------------------------------------------
# Hydro reservoir fill level via Nord Pool
# ---------------------------------------------------------------------------

def fetch_hydro_reservoir_from_csv(csv_path: Path, start: str, end: str) -> pd.DataFrame:
    """Parse a Nord Pool hydro-reservoir CSV export.

    Download from:
      https://www.nordpoolgroup.com/en/Market-data1/Power-system-data/hydro-reservoir1/ALL/Hourly/?view=table
    (click the download icon, choose CSV).

    Expected CSV format (Nord Pool export):
      Date/Time | NO1 | NO2 | NO3 | NO4 | NO5   (values: % fill, 0–100)

    Returns hourly DataFrame indexed by UTC timestamp, columns NO1..NO5.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Nord Pool reservoir CSV not found: {csv_path}\n"
            "Download it from:\n"
            "  https://www.nordpoolgroup.com/en/Market-data1/Power-system-data/"
            "hydro-reservoir1/ALL/Hourly/?view=table\n"
            "and place it at the path shown above."
        )

    df = pd.read_csv(csv_path, sep=None, engine="python")

    time_col = df.columns[0]
    df["_time"] = pd.to_datetime(df[time_col], dayfirst=True, errors="coerce", utc=True)
    df = df.dropna(subset=["_time"]).set_index("_time").drop(columns=[time_col])

    no_cols = [c for c in df.columns if c.strip().upper() in {"NO1", "NO2", "NO3", "NO4", "NO5"}]
    df = df[no_cols].copy()
    df.columns = [c.strip().upper() for c in df.columns]
    df = df.apply(pd.to_numeric, errors="coerce")

    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    df = df.reindex(idx)
    df.index.name = "time"
    return df


def fetch_hydro_reservoir_api(start: str, end: str, api_key: str) -> pd.DataFrame:
    """Fetch hydro reservoir data from the Nord Pool Data Portal REST API.

    Requires a free account: https://www.nordpoolgroup.com/en/services/power-market-data-services/dataportalregistration/

    Returns hourly DataFrame indexed by UTC timestamp, columns NO1..NO5, values in % fill.
    """
    base_url = "https://data.nordpoolgroup.com/api/v1/power-system/reservoir"

    start_dt = pd.Timestamp(start).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_dt = pd.Timestamp(end).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "from": start_dt,
        "to": end_dt,
        "areas": "NO1,NO2,NO3,NO4,NO5",
        "resolution": "hourly",
    }

    resp = _get_with_retry(base_url, params=params, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
    data = resp.json()

    records = data.get("data", data)
    df = pd.DataFrame(records)

    time_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
    df["_time"] = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    df = df.dropna(subset=["_time"]).set_index("_time").drop(columns=[time_col])

    no_cols = [c for c in df.columns if c.upper() in {"NO1", "NO2", "NO3", "NO4", "NO5"}]
    df = df[no_cols].apply(pd.to_numeric, errors="coerce")
    df.columns = [c.upper() for c in df.columns]

    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    df = df.reindex(idx)
    df.index.name = "time"
    return df


# ---------------------------------------------------------------------------
# Hydro inflow via NVE HydAPI
# ---------------------------------------------------------------------------

NVE_API_BASE = "https://hydapi.nve.no/api/v1/Observations"

# NVE parameter code 1002 = discharge / runoff (m³/s)
NVE_PARAM_DISCHARGE = 1002


def _nve_fetch_station(
    station_id: str,
    start: str,
    end: str,
    api_key: str,
    parameter: int = NVE_PARAM_DISCHARGE,
) -> pd.Series:
    """Fetch hourly observations from one NVE station. Returns a UTC-indexed Series."""
    start_str = pd.Timestamp(start).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = pd.Timestamp(end).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "StationId": station_id,
        "Parameter": parameter,
        "ResolutionTime": 60,
        "ReferenceTime": f"{start_str}/{end_str}",
    }
    resp = _get_with_retry(NVE_API_BASE, params=params, headers={"X-API-Key": api_key, "Accept": "application/json"}, timeout=60)
    data = resp.json()

    observations = []
    for series in data.get("data", []):
        observations.extend(series.get("observations", []))

    if not observations:
        return pd.Series(dtype=float, name=station_id)

    times = pd.to_datetime([o["time"] for o in observations], utc=True, errors="coerce")
    values = pd.to_numeric([o["value"] for o in observations], errors="coerce")
    return pd.Series(values, index=times, name=station_id, dtype=float)


def fetch_hydro_inflow(
    stations_by_zone: dict[str, list[str]],
    start: str,
    end: str,
    api_key: str,
) -> pd.DataFrame:
    """Fetch hourly discharge (m³/s) for NVE stations, averaged by zone.

    ``stations_by_zone`` maps zone names to lists of NVE station IDs.
    Find station IDs for your area at https://sildre.nve.no

    Example:
      stations_by_zone = {
          "NO1": ["12.171.0"],         # Glomma at Elverum
          "NO2": ["026.3.0"],          # Otra at Vigeland
          "NO3": ["122.9.0"],          # Orkla
          "NO4": ["163.5.0"],          # Målselv
          "NO5": ["072.5.0"],          # Vosso
      }

    Returns a DataFrame with one column per zone (zone-average discharge), hourly UTC index.
    """
    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    df = pd.DataFrame(index=idx)

    for zone, station_ids in sorted(stations_by_zone.items()):
        if not station_ids:
            continue

        zone_series: list[pd.Series] = []
        for sid in station_ids:
            try:
                ser = _nve_fetch_station(sid, start, end, api_key)
                if not ser.empty:
                    zone_series.append(ser.reindex(idx))
            except requests.HTTPError as exc:
                print(f"  Warning: NVE station {sid} ({zone}) returned {exc}; skipping.")
            time.sleep(0.2)

        if zone_series:
            df[zone] = pd.concat(zone_series, axis=1).mean(axis=1)

    df.index.name = "time"
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch supplemental hydro data for Baseload pipeline.")
    parser.add_argument("--config", required=True, help="Path to YAML config file.")
    parser.add_argument("--skip-reservoir", action="store_true", help="Skip Nord Pool hydro reservoir fetch.")
    parser.add_argument("--skip-inflow", action="store_true", help="Skip NVE hydro inflow fetch.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    start = cfg["date_range"]["start"]
    end = cfg["date_range"]["end"]
    sup_cfg = cfg.get("supplemental", {})

    # --- Hydro reservoir -------------------------------------------------------
    if not args.skip_reservoir:
        res_cfg = sup_cfg.get("hydro_reservoir", {})
        api_key = res_cfg.get("api_key") or os.environ.get("NORDPOOL_API_KEY")
        csv_path_str = res_cfg.get("csv_path")

        if csv_path_str:
            print(f"Loading hydro reservoir from CSV: {csv_path_str}")
            csv_path = Path(csv_path_str)
            try:
                res_df = fetch_hydro_reservoir_from_csv(csv_path, start, end)
                out_path = paths["processed"] / "hydro_reservoir.parquet"
                res_df.to_parquet(out_path)
                print(f"  Saved hydro reservoir -> {out_path}  shape={res_df.shape}")
            except FileNotFoundError as exc:
                print(f"  Skipping reservoir: {exc}")
        elif api_key:
            print("Fetching hydro reservoir from Nord Pool API...")
            try:
                res_df = fetch_hydro_reservoir_api(start, end, api_key)
                out_path = paths["processed"] / "hydro_reservoir.parquet"
                res_df.to_parquet(out_path)
                print(f"  Saved hydro reservoir -> {out_path}  shape={res_df.shape}")
            except requests.HTTPError as exc:
                print(f"  Nord Pool API error: {exc}. Skipping reservoir.")
        else:
            print(
                "Skipping hydro reservoir — no CSV path or API key configured.\n"
                "  Option A: Download CSV from Nord Pool and set supplemental.hydro_reservoir.csv_path\n"
                "  Option B: Set supplemental.hydro_reservoir.api_key or NORDPOOL_API_KEY env var."
            )
    else:
        print("Skipping hydro reservoir (--skip-reservoir).")

    # --- Hydro inflow ----------------------------------------------------------
    if not args.skip_inflow:
        inflow_cfg = sup_cfg.get("hydro_inflow", {})
        api_key = inflow_cfg.get("api_key") or os.environ.get("NVE_API_KEY")
        stations = inflow_cfg.get("stations", {})
        has_stations = any(v for v in stations.values())

        if api_key and has_stations:
            print("Fetching hydro inflow from NVE HydAPI...")
            try:
                inflow_df = fetch_hydro_inflow(stations, start, end, api_key)
                if not inflow_df.empty:
                    out_path = paths["processed"] / "hydro_inflow.parquet"
                    inflow_df.to_parquet(out_path)
                    print(f"  Saved hydro inflow -> {out_path}  shape={inflow_df.shape}")
                else:
                    print("  NVE returned no data for configured stations.")
            except requests.HTTPError as exc:
                print(f"  NVE API error: {exc}. Skipping inflow.")
        elif not api_key:
            print(
                "Skipping hydro inflow — no NVE API key.\n"
                "  Register for a free key at https://hydapi.nve.no/Users\n"
                "  Then set supplemental.hydro_inflow.api_key or NVE_API_KEY env var."
            )
        else:
            print(
                "Skipping hydro inflow — no station IDs configured.\n"
                "  Find station IDs at https://sildre.nve.no\n"
                "  Then set supplemental.hydro_inflow.stations in your config."
            )
    else:
        print("Skipping hydro inflow (--skip-inflow).")

    print("\nDone.")


if __name__ == "__main__":
    main()
