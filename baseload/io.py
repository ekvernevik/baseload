"""Standardised I/O helpers for the Baseload Phase 1 MVP pipeline.

Every artifact that crosses a pipeline boundary should be read and written
through this module so that file paths are derived in one place and schema
validation runs automatically at both load and save boundaries.

Intermediate artifacts  (data/processed/)
------------------------------------------
prices.parquet           Flat wide: columns = zone names, EUR/MWh.
load.parquet             Flat wide: columns = zone names, MW.
actgen.parquet           MultiIndex wide: level 0 = zone, level 1 = type, MW.
transmission.parquet     Flat wide: columns = "NOx-NOy" net-flow pairs, MW.
external_balance.parquet Flat wide: columns = zone names, net import MW.

Final artifacts  (artifacts/tables/)
--------------------------------------
valuation_pf.parquet/.csv
valuation_rh.parquet/.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schemas import SCHEMA_BUILDERS, SCHEMA_REGISTRY, DataFrameSchema, MultiIndexDataFrameSchema
from .validators import (
    SchemaError,
    validate_dataframe,
    validate_multiindex_dataframe,
)


# ---------------------------------------------------------------------------
# Core parquet helpers
# ---------------------------------------------------------------------------

def read_parquet(
    path: Path,
    schema_name: str | None = None,
    *,
    validate: bool = True,
    zones: list[str] | None = None,
    freq: str = "h",
) -> pd.DataFrame:
    """Read a parquet file and optionally validate it against a named schema.

    ``zones`` (interpreted as pairs for the "transmission" schema) and ``freq``
    select a schema built for that exact zone/pair set and target resolution
    via ``SCHEMA_BUILDERS`` instead of the NO1-NO5 hourly default in
    ``SCHEMA_REGISTRY``.
    """
    if not path.exists():
        raise FileNotFoundError(f"Parquet file not found: {path}")

    df = pd.read_parquet(path)

    if validate and schema_name is not None:
        schema = _get_schema(schema_name, path, zones=zones, freq=freq)
        _validate(df, schema)

    return df


def write_parquet(
    df: pd.DataFrame,
    path: Path,
    schema_name: str | None = None,
    *,
    validate: bool = True,
    also_csv: bool = False,
    zones: list[str] | None = None,
    freq: str = "h",
) -> None:
    """Validate *df* then write it to *path* as parquet.

    Validation runs before any bytes are written so a bad DataFrame never
    produces a corrupt artifact on disk. ``zones`` (interpreted as pairs for
    the "transmission" schema) and ``freq`` select a schema built for that
    exact zone/pair set and target resolution via ``SCHEMA_BUILDERS`` instead
    of the NO1-NO5 hourly default.
    """
    if validate and schema_name is not None:
        schema = _get_schema(schema_name, path, zones=zones, freq=freq)
        _validate(df, schema)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=True)

    if also_csv:
        df.to_csv(path.with_suffix(".csv"), index=True)


# ---------------------------------------------------------------------------
# Intermediate artifact wrappers  (data/processed/)
# ---------------------------------------------------------------------------

def read_prices(paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> pd.DataFrame:
    return read_parquet(paths["processed"] / "prices.parquet", schema_name="prices", validate=validate, zones=zones, freq=freq)


def write_prices(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> None:
    write_parquet(df, paths["processed"] / "prices.parquet", schema_name="prices", validate=validate, zones=zones, freq=freq)


def read_load(paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> pd.DataFrame:
    return read_parquet(paths["processed"] / "load.parquet", schema_name="load", validate=validate, zones=zones, freq=freq)


def write_load(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> None:
    write_parquet(df, paths["processed"] / "load.parquet", schema_name="load", validate=validate, zones=zones, freq=freq)


def read_gen(paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> pd.DataFrame:
    """Load ``data/processed/actgen.parquet``.

    Returns a MultiIndex-column DataFrame (level 0 = zone, level 1 = type).
    """
    return read_parquet(paths["processed"] / "actgen.parquet", schema_name="actgen", validate=validate, zones=zones, freq=freq)


def write_gen(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> None:
    """Persist a MultiIndex generation DataFrame to ``data/processed/actgen.parquet``."""
    if validate:
        schema = _get_schema("actgen", paths["processed"] / "actgen.parquet", zones=zones, freq=freq)
        validate_multiindex_dataframe(df, schema, raise_on_error=True)
    path = paths["processed"] / "actgen.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=True)


def read_transmission(paths: dict[str, Path], *, validate: bool = True, pairs: list[str] | None = None, freq: str = "h") -> pd.DataFrame:
    """Load ``data/processed/transmission.parquet`` (internal net flows)."""
    return read_parquet(paths["processed"] / "transmission.parquet", schema_name="transmission", validate=validate, zones=pairs, freq=freq)


def write_transmission(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, pairs: list[str] | None = None, freq: str = "h") -> None:
    """Persist the internal transmission DataFrame to ``data/processed/transmission.parquet``."""
    write_parquet(df, paths["processed"] / "transmission.parquet", schema_name="transmission", validate=validate, zones=pairs, freq=freq)


def read_external_balance(paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> pd.DataFrame:
    """Load ``data/processed/external_balance.parquet`` (net import per zone)."""
    return read_parquet(paths["processed"] / "external_balance.parquet", schema_name="external_balance", validate=validate, zones=zones, freq=freq)


def write_external_balance(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, zones: list[str] | None = None, freq: str = "h") -> None:
    """Persist the external balance DataFrame to ``data/processed/external_balance.parquet``."""
    write_parquet(df, paths["processed"] / "external_balance.parquet", schema_name="external_balance", validate=validate, zones=zones, freq=freq)


def read_reserves(paths: dict[str, Path], market: str, *, validate: bool = True, freq: str = "h") -> pd.DataFrame:
    """Load ``data/processed/reserves_<market>.parquet`` (mFRR EAM / aFRR / FCR-D).

    Returns a MultiIndex-column DataFrame (level 0 = zone, level 1 = field).
    """
    return read_parquet(
        paths["processed"] / f"reserves_{market}.parquet",
        schema_name=f"reserves_{market}", validate=validate, freq=freq,
    )


def write_reserves(df: pd.DataFrame, paths: dict[str, Path], market: str, *, validate: bool = True, freq: str = "h") -> None:
    """Persist a reserve-market MultiIndex DataFrame for *market*."""
    write_parquet(
        df, paths["processed"] / f"reserves_{market}.parquet",
        schema_name=f"reserves_{market}", validate=validate, freq=freq,
    )


# ---------------------------------------------------------------------------
# Final artifact wrappers  (artifacts/tables/)
# ---------------------------------------------------------------------------

def read_valuation_pf(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    return read_parquet(paths["tables"] / "valuation_pf.parquet", schema_name="valuation_pf", validate=validate)


def write_valuation_pf(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, also_csv: bool = True) -> None:
    write_parquet(df, paths["tables"] / "valuation_pf.parquet", schema_name="valuation_pf", validate=validate, also_csv=also_csv)


def read_valuation_pf_multiyear(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    return read_parquet(paths["tables"] / "valuation_pf_multiyear.parquet", schema_name="valuation_pf_multiyear", validate=validate)


def write_valuation_pf_multiyear(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, also_csv: bool = True) -> None:
    write_parquet(df, paths["tables"] / "valuation_pf_multiyear.parquet", schema_name="valuation_pf_multiyear", validate=validate, also_csv=also_csv)


def read_valuation_rh(paths: dict[str, Path], *, validate: bool = True) -> pd.DataFrame:
    return read_parquet(paths["tables"] / "valuation_rh.parquet", schema_name="valuation_rh", validate=validate)


def write_valuation_rh(df: pd.DataFrame, paths: dict[str, Path], *, validate: bool = True, also_csv: bool = True) -> None:
    write_parquet(df, paths["tables"] / "valuation_rh.parquet", schema_name="valuation_rh", validate=validate, also_csv=also_csv)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_schema(
    schema_name: str,
    path: Path,
    *,
    zones: list[str] | None = None,
    freq: str = "h",
) -> DataFrameSchema | MultiIndexDataFrameSchema:
    if schema_name not in SCHEMA_REGISTRY:
        raise KeyError(
            f"Unknown schema '{schema_name}' for path {path}. "
            f"Available: {sorted(SCHEMA_REGISTRY)}."
        )
    # Build a config-specific schema when the caller pins zones/pairs or a
    # non-hourly resolution; otherwise use the NO1-NO5 hourly default.
    if zones is not None or freq != "h":
        builder = SCHEMA_BUILDERS.get(schema_name)
        if builder is not None:
            return builder(zones, freq)
    return SCHEMA_REGISTRY[schema_name]


def _validate(df: pd.DataFrame, schema: DataFrameSchema | MultiIndexDataFrameSchema) -> None:
    """Dispatch to the correct validator based on schema type."""
    if isinstance(schema, MultiIndexDataFrameSchema):
        validate_multiindex_dataframe(df, schema, raise_on_error=True)
    else:
        validate_dataframe(df, schema, raise_on_error=True)
