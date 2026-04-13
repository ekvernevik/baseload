"""Standardised I/O helpers for the Baseload Phase 1 MVP pipeline.

Every artifact that crosses a pipeline boundary (raw → processed →
artifacts) should be read and written through this module so that:

* File paths are derived from the config in one place.
* Schema validation runs automatically at both load and save boundaries.
* Intermediate vs. final artifacts are clearly distinguished.

Intermediate artifacts  (data/processed/)
------------------------------------------
prices.parquet          — hourly UTC day-ahead prices, EUR/MWh, wide (columns = zones).
load.parquet            — hourly UTC actual total load, MW, wide (columns = zones).
gen_{zone}.parquet      — hourly UTC generation per production type, MW, wide
                          (columns = ENTSO-E production type names).  One file per zone.
transmission.parquet    — hourly UTC cross-border physical flows, MW, wide
                          (columns = "OutArea>InArea" border pair strings, all zones combined).

Final artifacts  (artifacts/tables/, artifacts/figures/, …)
-------------------------------------------------------------
valuation_pf.parquet/.csv  — perfect-foresight BESS valuation.
valuation_rh.parquet/.csv  — rolling-horizon BESS valuation.

Note on gen and transmission ingestion
---------------------------------------
The raw ENTSO-E gen and transmission files use a tall format (one row per hour
per production type or border pair).  They must be pivoted to wide format before
the schemas in this module can be applied.  ``ingest_entsoe.py`` is responsible
for that pivot; the helpers here only handle validated read/write of the output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .schemas import SCHEMA_REGISTRY, DataFrameSchema
from .validators import SchemaError, validate_dataframe


# ---------------------------------------------------------------------------
# Core parquet helpers
# ---------------------------------------------------------------------------

def read_parquet(
    path: Path,
    schema_name: str | None = None,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Read a parquet file and optionally validate it against a named schema.

    Parameters
    ----------
    path:
        Path to the ``.parquet`` file.
    schema_name:
        Key into ``SCHEMA_REGISTRY`` (e.g. ``"prices"``, ``"load"``,
        ``"gen"``, ``"transmission"``).  When provided and ``validate=True``
        a ``SchemaError`` is raised if the DataFrame does not conform.
        When ``None`` validation is skipped.
    validate:
        Set to ``False`` to suppress schema validation.

    Returns
    -------
    pd.DataFrame
    """
    if not path.exists():
        raise FileNotFoundError(f"Parquet file not found: {path}")

    df = pd.read_parquet(path)

    if validate and schema_name is not None:
        schema = _get_schema(schema_name, path)
        validate_dataframe(df, schema, raise_on_error=True)

    return df


def write_parquet(
    df: pd.DataFrame,
    path: Path,
    schema_name: str | None = None,
    *,
    validate: bool = True,
    also_csv: bool = False,
) -> None:
    """Validate *df* and write it to *path* as parquet.

    Validation runs *before* any bytes are written so a bad DataFrame never
    produces a corrupt or misleading artifact on disk.

    Parameters
    ----------
    df:
        DataFrame to persist.
    path:
        Destination path (parent directories are created automatically).
    schema_name:
        Key into ``SCHEMA_REGISTRY``.  When provided and ``validate=True``
        a ``SchemaError`` is raised before any bytes are written.
    validate:
        Set to ``False`` to bypass schema validation.
    also_csv:
        When ``True`` also write a ``.csv`` alongside the parquet file.
        Useful for final tables.
    """
    if validate and schema_name is not None:
        schema = _get_schema(schema_name, path)
        validate_dataframe(df, schema, raise_on_error=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=True)

    if also_csv:
        df.to_csv(path.with_suffix(".csv"), index=True)


# ---------------------------------------------------------------------------
# Intermediate artifact wrappers  (data/processed/)
# ---------------------------------------------------------------------------

def read_prices(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    """Load ``data/processed/prices.parquet`` with schema validation."""
    return read_parquet(
        paths["processed"] / "prices.parquet",
        schema_name="prices",
        validate=validate,
    )


def write_prices(
    df: pd.DataFrame,
    paths: dict[str, Path],
    *,
    validate: bool = True,
) -> None:
    """Persist a prices DataFrame to ``data/processed/prices.parquet``."""
    write_parquet(
        df,
        paths["processed"] / "prices.parquet",
        schema_name="prices",
        validate=validate,
    )


def read_load(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    """Load ``data/processed/load.parquet`` with schema validation."""
    return read_parquet(
        paths["processed"] / "load.parquet",
        schema_name="load",
        validate=validate,
    )


def write_load(
    df: pd.DataFrame,
    paths: dict[str, Path],
    *,
    validate: bool = True,
) -> None:
    """Persist a load DataFrame to ``data/processed/load.parquet``."""
    write_parquet(
        df,
        paths["processed"] / "load.parquet",
        schema_name="load",
        validate=validate,
    )


def read_gen(paths: dict[str, Path], zone: str, *, validate: bool = True) -> pd.DataFrame:
    """Load ``data/processed/gen_{zone}.parquet`` with schema validation.

    Parameters
    ----------
    zone:
        One of ``"NO1"`` … ``"NO5"``.  Each zone has its own file because
        the set of active production types differs per zone.
    """
    return read_parquet(
        paths["processed"] / f"gen_{zone}.parquet",
        schema_name="gen",
        validate=validate,
    )


def write_gen(
    df: pd.DataFrame,
    paths: dict[str, Path],
    zone: str,
    *,
    validate: bool = True,
) -> None:
    """Persist a per-zone generation DataFrame to ``data/processed/gen_{zone}.parquet``.

    Parameters
    ----------
    zone:
        One of ``"NO1"`` … ``"NO5"``.
    """
    write_parquet(
        df,
        paths["processed"] / f"gen_{zone}.parquet",
        schema_name="gen",
        validate=validate,
    )


def read_transmission(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    """Load ``data/processed/transmission.parquet`` with schema validation.

    The file contains all active border pairs across all five zones combined,
    with duplicates removed (e.g. ``"NO1>NO2"`` appears once even though it
    shows up in both the NO1 and NO2 raw files).
    """
    return read_parquet(
        paths["processed"] / "transmission.parquet",
        schema_name="transmission",
        validate=validate,
    )


def write_transmission(
    df: pd.DataFrame,
    paths: dict[str, Path],
    *,
    validate: bool = True,
) -> None:
    """Persist the combined transmission DataFrame to ``data/processed/transmission.parquet``."""
    write_parquet(
        df,
        paths["processed"] / "transmission.parquet",
        schema_name="transmission",
        validate=validate,
    )


# ---------------------------------------------------------------------------
# Final artifact wrappers  (artifacts/tables/)
# ---------------------------------------------------------------------------

def read_valuation_pf(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    return read_parquet(
        paths["tables"] / "valuation_pf.parquet",
        schema_name="valuation_pf",
        validate=validate,
    )


def write_valuation_pf(
    df: pd.DataFrame,
    paths: dict[str, Path],
    *,
    validate: bool = True,
    also_csv: bool = True,
) -> None:
    write_parquet(
        df,
        paths["tables"] / "valuation_pf.parquet",
        schema_name="valuation_pf",
        validate=validate,
        also_csv=also_csv,
    )


def read_valuation_rh(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    return read_parquet(
        paths["tables"] / "valuation_rh.parquet",
        schema_name="valuation_rh",
        validate=validate,
    )


def write_valuation_rh(
    df: pd.DataFrame,
    paths: dict[str, Path],
    *,
    validate: bool = True,
    also_csv: bool = True,
) -> None:
    write_parquet(
        df,
        paths["tables"] / "valuation_rh.parquet",
        schema_name="valuation_rh",
        validate=validate,
        also_csv=also_csv,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_schema(schema_name: str, path: Path) -> DataFrameSchema:
    if schema_name not in SCHEMA_REGISTRY:
        raise KeyError(
            f"Unknown schema '{schema_name}' for path {path}. "
            f"Available: {sorted(SCHEMA_REGISTRY)}."
        )
    return SCHEMA_REGISTRY[schema_name]
