"""Shared helpers for the Baseload Phase 1 MVP pipeline."""
from __future__ import annotations

import hashlib
import json
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
]
DEFAULT_VALUE_COLS = ["price", "value", "eur_mwh", "day_ahead_price", "spot_price"]


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


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    clean_map = {c.lower().strip().replace(" ", "_"): c for c in columns}
    for cand in candidates:
        if cand in clean_map:
            return clean_map[cand]
    return None


def standardize_series(df: pd.DataFrame, zone: str, value_name: str) -> pd.Series:
    """Return hourly UTC series from raw table."""
    cols = list(df.columns)
    time_col = _find_column(cols, DEFAULT_TIME_COLS)
    if time_col is None:
        time_col = cols[0]

    value_col = _find_column(cols, DEFAULT_VALUE_COLS)
    if value_col is None:
        numeric_candidates = [c for c in cols if c != time_col]
        if not numeric_candidates:
            raise ValueError(f"No value column found for zone={zone}")
        value_col = numeric_candidates[0]

    times = pd.to_datetime(df[time_col], utc=True, errors="coerce")
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
