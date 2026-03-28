"""Standardised I/O helpers for the Baseload Phase 1 MVP pipeline.
 
Every artifact that crosses a pipeline boundary (raw → processed →
artifacts) should be read and written through this module so that:
 
* File paths are derived from the config in one place.
* Schema validation runs automatically at both load and save boundaries.
* Intermediate vs. final artifacts are clearly distinguished.
 
Intermediate artifacts
----------------------
``data/processed/`` — parquet files consumed by downstream scripts but
not delivered to end-users (e.g. ``prices.parquet``).
 
Final artifacts
---------------
``artifacts/tables/``  — parquet + CSV tables shown in reports.
``artifacts/figures/`` — PNG plots.
``artifacts/memo/``    — markdown / HTML summaries.
``artifacts/alerts/``  — JSON alert payloads.
"""
from __future__ import annotations
 
from pathlib import Path
from typing import Any
 
import pandas as pd
 
from .schemas import SCHEMA_REGISTRY, DataFrameSchema
from .validators import SchemaError, validate_dataframe
 
 
# ---------------------------------------------------------------------------
# Parquet helpers (intermediate + final tables)
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
        Key into ``SCHEMA_REGISTRY`` (e.g. ``"prices"``).  When provided
        and ``validate=True`` a ``SchemaError`` is raised if the DataFrame
        does not conform.  When ``None`` validation is skipped regardless
        of the *validate* flag.
    validate:
        Set to ``False`` to suppress schema validation (useful in tests or
        exploratory scripts).
 
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
        When ``True`` also write a ``.csv`` alongside the parquet file
        (same stem, same directory).  Useful for final tables.
    """
    if validate and schema_name is not None:
        schema = _get_schema(schema_name, path)
        validate_dataframe(df, schema, raise_on_error=True)
 
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=True)
 
    if also_csv:
        df.to_csv(path.with_suffix(".csv"), index=True)
 
 
# ---------------------------------------------------------------------------
# Prices convenience wrappers
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
 
 
# ---------------------------------------------------------------------------
# Valuation table wrappers
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