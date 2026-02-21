"""Utility helpers for pre-processing ENTSO-E Transparency Platform CSV exports.

The main pipeline already handles ingestion via *ingest_entsoe.py* and
:func:`baseload.pipeline_utils.standardize_series`.  The helpers below are
thin wrappers that can be used for ad-hoc exploration or testing.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from baseload.pipeline_utils import parse_csv_flexible, standardize_series


def load_entsoe_zone(csv_path: str | Path, zone: str) -> pd.Series:
    """Read a single ENTSO-E day-ahead price CSV and return an hourly UTC series.

    Parameters
    ----------
    csv_path:
        Path to the raw CSV (e.g. ``data/raw/no1_prices.csv``).
    zone:
        Bidding-zone label used as the series name (e.g. ``"NO1"``).

    Returns
    -------
    pd.Series
        Hourly price series indexed by UTC timestamps.
    """
    df = parse_csv_flexible(Path(csv_path))
    return standardize_series(df, zone=zone, value_name="price")