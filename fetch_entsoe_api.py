#!/usr/bin/env python3
"""One-off tool: pull real ENTSO-E data via the Transparency Platform API.

Unlike ``ingest_entsoe.py`` (which parses manually-exported GUI CSVs), this
script calls the ENTSO-E API directly and writes CSVs in the same format the
existing pipeline already understands, so no changes to the base pipeline are
needed. Its main purpose is fetching genuine 15-min resolution data (the GUI
export defaults to hourly for most data types).

Usage:
    python fetch_entsoe_api.py --start 2025-10-01 --end 2025-10-07
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd
from entsoe import EntsoePandasClient

_ZONE_AREA = {
    "NO1": "NO_1",
    "NO2": "NO_2",
    "NO3": "NO_3",
    "NO4": "NO_4",
    "NO5": "NO_5",
}

# Norway's internal bidding-zone interconnections (Statnett grid topology).
_INTERNAL_PAIRS = [
    ("NO1", "NO2"), ("NO1", "NO3"), ("NO1", "NO5"),
    ("NO2", "NO5"), ("NO3", "NO4"), ("NO3", "NO5"),
]

# External interconnectors: (NO zone, ENTSO-E area code, label used in CSV).
_EXTERNAL_PAIRS = [
    ("NO1", "SE_3", "SE3"),
    ("NO2", "SE_3", "SE3"),
    ("NO2", "DK_1", "DK1"),
    ("NO2", "DE_LU", "DE_LU"),
    ("NO2", "GB", "GB"),
    ("NO3", "SE_2", "SE2"),
    ("NO4", "SE_1", "SE1"),
    ("NO4", "SE_2", "SE2"),
    ("NO4", "FI", "FI"),
]

# entsoe-py mislabels this PSR type; realign it with the pipeline's expected name.
_GEN_TYPE_FIXUPS = {
    "Hydro Run-of-river and poundage": "Hydro Run-of-river and pondage",
}


def _read_api_key() -> str:
    key = os.environ.get("ENTSOE_API_KEY")
    if key:
        return key
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ENTSOE_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("Set ENTSOE_API_KEY (env var or .env) before running this script.")


def _fmt_mtu(idx: pd.DatetimeIndex) -> pd.Series:
    """Format a naive DatetimeIndex as ENTSO-E-style 'start - end' range strings."""
    step = idx.to_series().diff().mode().iloc[0]
    starts = idx.strftime("%d/%m/%Y %H:%M:%S")
    ends = (idx + step).strftime("%d/%m/%Y %H:%M:%S")
    return starts + " - " + ends


def _densify_15min(obj):
    """Reindex to a uniform 15-min grid, forward-filling pre-rollout hourly data.

    Before 1 Oct 2025 ENTSO-E only published hourly values; each hour's value
    is genuinely constant across its four 15-min slots, so forward-filling is
    an accurate densification rather than an approximation. From October
    onward the source is already 15-min, so this is a no-op there.
    """
    obj = obj[~obj.index.duplicated(keep="first")]  # DST fall-back repeats an hour
    full_idx = pd.date_range(obj.index.min(), obj.index.max(), freq="15min")
    return obj.reindex(full_idx).ffill()


def _retry(fn, *args, attempts: int = 4, **kwargs):
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if attempt == attempts:
                raise
            print(f"    retrying after error (attempt {attempt}/{attempts})...")
            time.sleep(delay)
            delay *= 2


def fetch_prices(client: EntsoePandasClient, zone: str, start, end, out_dir: Path) -> tuple[Path, int]:
    s = _retry(client.query_day_ahead_prices, _ZONE_AREA[zone], start=start, end=end)
    s = _densify_15min(s.tz_localize(None))
    df = pd.DataFrame({
        "MTU (CET/CEST)": _fmt_mtu(s.index),
        "Area": f"BZN|{zone}",
        "Day-ahead Price (EUR/MWh)": s.values,
    })
    path = out_dir / "prices" / f"{zone.lower()}_prices.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path, len(df)


def fetch_load(client: EntsoePandasClient, zone: str, start, end, out_dir: Path) -> tuple[Path, int]:
    raw = _retry(client.query_load, _ZONE_AREA[zone], start=start, end=end)
    raw = raw.set_axis(raw.index.tz_localize(None))
    raw = _densify_15min(raw["Actual Load"])
    df = pd.DataFrame({
        "MTU (CET/CEST)": _fmt_mtu(raw.index),
        "Area": f"BZN|{zone}",
        "Actual Total Load (MW)": raw.values,
    })
    path = out_dir / "load" / f"{zone.lower()}_load.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path, len(df)


def fetch_actgen(client: EntsoePandasClient, zone: str, start, end, out_dir: Path) -> tuple[Path, int]:
    raw = _retry(client.query_generation, _ZONE_AREA[zone], start=start, end=end)
    raw = raw.set_axis(raw.index.tz_convert("UTC").tz_localize(None))
    raw = _densify_15min(raw)

    if isinstance(raw.columns, pd.MultiIndex):
        # Types with pumping/storage (e.g. Hydro Pumped Storage) report separate
        # "Actual Aggregated" (generating) and "Actual Consumption" (pumping)
        # columns. The pipeline's generation schema only accepts non-negative
        # values, so keep generation only and drop the pumping/consumption side.
        types = sorted(raw.columns.get_level_values(0).unique())
        raw = raw.reindex(columns=[(t, "Actual Aggregated") for t in types], fill_value=0)
        raw.columns = types

    mtu = _fmt_mtu(raw.index)
    frames = [
        pd.DataFrame({
            "MTU (UTC)": mtu,
            "Area": f"BZN|{zone}",
            "Production Type": _GEN_TYPE_FIXUPS.get(col, col),
            "Generation (MW)": raw[col].values,
        })
        for col in raw.columns
    ]
    out = pd.concat(frames, ignore_index=True)
    path = out_dir / "actgen_ppt" / f"{zone.lower()}_actgen_ppt.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path, len(out)


def _flow_net(client: EntsoePandasClient, area_a: str, area_b: str, start, end) -> pd.Series:
    """Net physical flow a->b (positive = flowing toward b), in naive UTC."""
    a_to_b = _retry(client.query_crossborder_flows, area_a, area_b, start=start, end=end)
    b_to_a = _retry(client.query_crossborder_flows, area_b, area_a, start=start, end=end)
    idx = a_to_b.index.union(b_to_a.index)
    net = a_to_b.reindex(idx).fillna(0) - b_to_a.reindex(idx).fillna(0)
    net = net.tz_convert("UTC").tz_localize(None)
    return _densify_15min(net)


def fetch_transmission(client: EntsoePandasClient, start, end, out_dir: Path) -> dict[str, tuple[Path, int]]:
    zone_rows: dict[str, list[pd.DataFrame]] = {z: [] for z in _ZONE_AREA}

    for a, b in _INTERNAL_PAIRS:
        try:
            net = _flow_net(client, _ZONE_AREA[a], _ZONE_AREA[b], start, end)
        except Exception as exc:
            print(f"  WARNING: skipping internal pair {a}-{b}: {exc}")
            continue
        row = pd.DataFrame({
            "MTU": _fmt_mtu(net.index),
            "Out Area": f"BZN|{a}",
            "In Area": f"BZN|{b}",
            "Physical Flow (MW)": net.values,
        })
        zone_rows[a].append(row)
        zone_rows[b].append(row)

    for no_zone, ext_area, ext_label in _EXTERNAL_PAIRS:
        try:
            net = _flow_net(client, _ZONE_AREA[no_zone], ext_area, start, end)
        except Exception as exc:
            print(f"  WARNING: skipping external pair {no_zone}-{ext_label}: {exc}")
            continue
        row = pd.DataFrame({
            "MTU": _fmt_mtu(net.index),
            "Out Area": f"BZN|{no_zone}",
            "In Area": f"BZN|{ext_label}",
            "Physical Flow (MW)": net.values,
        })
        zone_rows[no_zone].append(row)

    paths = {}
    for zone, rows in zone_rows.items():
        if not rows:
            continue
        out = pd.concat(rows, ignore_index=True)
        path = out_dir / "transmission" / f"{zone.lower()}_transmission.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(path, index=False)
        paths[zone] = (path, len(out))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--out-dir", default="data/raw_15min")
    parser.add_argument("--zones", nargs="+", default=list(_ZONE_AREA))
    parser.add_argument("--skip-transmission", action="store_true")
    args = parser.parse_args()

    client = EntsoePandasClient(api_key=_read_api_key())
    start = pd.Timestamp(args.start, tz="Europe/Oslo")
    # --end is a calendar date; include the whole day by advancing to the next
    # midnight and letting the API's inclusive truncation handle the rest.
    end = pd.Timestamp(args.end, tz="Europe/Oslo") + pd.Timedelta(days=1)
    out_dir = Path(args.out_dir)

    for zone in args.zones:
        print(f"[{zone}] prices...")
        path, n = fetch_prices(client, zone, start, end, out_dir)
        print(f"  -> {path} ({n} rows)")

        print(f"[{zone}] load...")
        path, n = fetch_load(client, zone, start, end, out_dir)
        print(f"  -> {path} ({n} rows)")

        print(f"[{zone}] generation...")
        path, n = fetch_actgen(client, zone, start, end, out_dir)
        print(f"  -> {path} ({n} rows)")

    if not args.skip_transmission:
        print("transmission (internal + external flows)...")
        for zone, (path, n) in fetch_transmission(client, start, end, out_dir).items():
            print(f"  [{zone}] -> {path} ({n} rows)")


if __name__ == "__main__":
    main()
