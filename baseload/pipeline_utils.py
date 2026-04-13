"""Shared helpers for the Baseload Phase 1 MVP pipeline."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


DEFAULT_TIME_COLS = [
    "datetime",
    "date_time",
    "timestamp",
    "time",
    "utc_time",
    "period_start",
    "mtu",
]
DEFAULT_VALUE_COLS = [
    "price",
    "value",
    "eur_mwh",
    "day_ahead_price",
    "spot_price",
    "day_ahead",
]


def load_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg


def parse_csv_flexible(path: Path) -> pd.DataFrame:
    """Parse CSV with tolerant delimiter and decimal handling."""
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    attempts = [
        dict(sep=None, engine="python"),
        dict(sep=";", decimal=",", engine="python"),
        dict(sep=",", decimal=".", engine="python"),
    ]
    last_err = None
    for kwargs in attempts:
        try:
            df = pd.read_csv(path, **kwargs)
            if df.shape[1] >= 2:
                return df
        except Exception as exc:  # pragma: no cover
            last_err = exc
    raise ValueError(f"Failed to parse CSV {path}. Last error: {last_err}")


def _normalize(name: str) -> str:
    """Collapse a column header to lower-case alphanumeric + underscores."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower().strip()).strip('_')


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    clean_map = {_normalize(c): c for c in columns}
    # exact match on normalized name
    for cand in candidates:
        if cand in clean_map:
            return clean_map[cand]
    # substring match: candidate appears inside a normalized column name
    for cand in candidates:
        for norm, orig in clean_map.items():
            if cand in norm:
                return orig
    return None


def standardize_series(df: pd.DataFrame, zone: str, value_name: str) -> pd.Series:
    """Return hourly UTC series from raw table.

    Handles ENTSO-E range timestamps (``"DD/MM/YYYY HH:MM:SS - ..."``),
    CET/CEST time-zone columns, and quarter-hourly to hourly aggregation.
    """
    cols = list(df.columns)
    time_col = _find_column(cols, DEFAULT_TIME_COLS)
    if time_col is None:
        time_col = cols[0]

    value_col = _find_column(cols, DEFAULT_VALUE_COLS)
    if value_col is None:
        # pick first column whose values are mostly numeric
        numeric_candidates = [
            c for c in cols
            if c != time_col
            and pd.to_numeric(df[c], errors="coerce").notna().sum() > len(df) * 0.5
        ]
        if not numeric_candidates:
            raise ValueError(f"No value column found for zone={zone}")
        value_col = numeric_candidates[0]

    # --- timestamp handling ---------------------------------------------------
    raw_times = df[time_col].astype(str)

    # ENTSO-E range format: "01/01/2025 00:00:00 - 01/01/2025 00:15:00"
    if raw_times.str.contains(' - ', na=False).any():
        raw_times = raw_times.str.split(' - ').str[0].str.strip()

    times = pd.to_datetime(raw_times, dayfirst=True, errors="coerce")

    # Detect source timezone from column header
    col_upper = time_col.upper()
    if "CET" in col_upper or "CEST" in col_upper:
        try:
            times = times.dt.tz_localize("CET", ambiguous="infer", nonexistent="shift_forward")
        except Exception:
            times = times.dt.tz_localize("CET", ambiguous=True, nonexistent="shift_forward")
        times = times.dt.tz_convert("UTC")
    elif times.dt.tz is None:
        times = times.dt.tz_localize("UTC")

    # --- value handling -------------------------------------------------------
    values = pd.to_numeric(df[value_col], errors="coerce")

    out = pd.Series(values.values, index=times, name=zone)
    out = out[~out.index.isna()].sort_index()
    if out.empty:
        raise ValueError(f"No valid timestamps in zone={zone}")

    out = out.groupby(level=0).mean()
    out = out.resample("h").mean()
    out.name = value_name
    return out


def align_hourly(table_by_zone: dict[str, pd.Series], start: str, end: str) -> pd.DataFrame:
    idx = pd.date_range(start=start, end=end, freq="h", tz="UTC")
    frame = pd.DataFrame(index=idx)
    for zone, ser in sorted(table_by_zone.items()):
        frame[zone] = ser.reindex(idx)
    frame.index.name = "time"
    return frame


def ensure_dirs(cfg: dict[str, Any]) -> dict[str, Path]:
    paths = cfg.get("paths", {})
    out = {
        "raw": Path(paths.get("raw_dir", "data/raw")),
        "processed": Path(paths.get("processed_dir", "data/processed")),
        "metadata": Path(paths.get("metadata_dir", "data/metadata")),
        "artifacts": Path(paths.get("artifacts_dir", "artifacts")),
    }
    out["tables"] = out["artifacts"] / "tables"
    out["figures"] = out["artifacts"] / "figures"
    out["alerts"] = out["artifacts"] / "alerts"
    out["memo"] = out["artifacts"] / "memo"
    for p in out.values():
        p.mkdir(parents=True, exist_ok=True)
    return out


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def consecutive_true_max(mask: pd.Series) -> int:
    max_run, run = 0, 0
    for val in mask.fillna(False).astype(bool).tolist():
        run = run + 1 if val else 0
        max_run = max(max_run, run)
    return max_run


def safe_div(a: float, b: float) -> float:
    if b == 0 or np.isnan(b):
        return float("nan")
    return a / b


# ---------------------------------------------------------------------------
# Schema-aware I/O additions (Phase 1 data-contract extension)
#
# The functions below extend pipeline_utils with validated read/write
# helpers. They delegate to the new src/data layer (schemas, validators, io)
# so all schema logic lives in one place.
# ---------------------------------------------------------------------------
 
def read_artifact(
    path: Path,
    schema_name: str | None = None,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Read any parquet artifact and optionally validate its schema.
 
    This is a thin convenience wrapper around ``baseload.data.io.read_parquet``
    that can be called directly from pipeline scripts without importing the
    full data sub-package.
 
    Parameters
    ----------
    path:
        Path to the ``.parquet`` file.
    schema_name:
        Optional key into the schema registry (``"prices"``,
        ``"valuation_pf"``, ``"valuation_rh"``).
    validate:
        Set ``False`` to skip validation (e.g. in exploratory notebooks).
    """
    from baseload.data.io import read_parquet  # lazy import keeps the module lightweight
    return read_parquet(path, schema_name=schema_name, validate=validate)
 
 
def write_artifact(
    df: pd.DataFrame,
    path: Path,
    schema_name: str | None = None,
    *,
    validate: bool = True,
    also_csv: bool = False,
) -> None:
    """Validate *df* and persist it to *path* as parquet.
 
    Parameters
    ----------
    df:
        DataFrame to save.
    path:
        Destination ``.parquet`` path (parent dirs created automatically).
    schema_name:
        Optional key into the schema registry.  Validation runs *before*
        any bytes are written so a bad DataFrame never produces a corrupt
        artifact.
    validate:
        Set ``False`` to skip validation.
    also_csv:
        When ``True`` also write a ``.csv`` alongside the parquet file.
    """
    from baseload.data.io import write_parquet  # lazy import
    write_parquet(df, path, schema_name=schema_name, validate=validate, also_csv=also_csv)
 
 
def assert_no_nulls(df: pd.DataFrame, context: str = "") -> None:
    """Raise ``ValueError`` if *df* contains any null values.
 
    Use this as a cheap early-exit guard immediately after loading raw data.
 
    Parameters
    ----------
    df:
        DataFrame to check.
    context:
        Short label included in the error message (e.g. script name or
        column group) to make the origin clear.
    """
    null_counts = df.isnull().sum()
    bad = null_counts[null_counts > 0]
    if not bad.empty:
        detail = ", ".join(f"{col}={n}" for col, n in bad.items())
        prefix = f"[{context}] " if context else ""
        raise ValueError(f"{prefix}Unexpected nulls found — {detail}")
 
 
def assert_utc_index(df: pd.DataFrame, context: str = "") -> None:
    """Raise ``ValueError`` if *df* does not have a UTC DatetimeIndex.
 
    Parameters
    ----------
    df:
        DataFrame whose index to check.
    context:
        Short label included in the error message.
    """
    idx = df.index
    prefix = f"[{context}] " if context else ""
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError(f"{prefix}Index must be a DatetimeIndex, got {type(idx).__name__}.")
    if idx.tz is None:
        raise ValueError(f"{prefix}DatetimeIndex must be timezone-aware (UTC expected).")
    import pytz, datetime
    tz = idx.tz
    utc_aliases = {pytz.UTC, datetime.timezone.utc}
    # also accept tzinfo whose zone string contains "UTC"
    tz_str = str(tz)
    if tz not in utc_aliases and "UTC" not in tz_str.upper():
        raise ValueError(f"{prefix}DatetimeIndex timezone must be UTC, got '{tz}'.")

