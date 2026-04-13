"""Validation helpers for the Baseload Phase 1 MVP pipeline.

All public functions raise ``SchemaError`` (a subclass of ``ValueError``)
on the *first* problem found so failures surface early and messages are
actionable.  Pass ``raise_on_error=False`` to collect all problems instead.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .schemas import ColumnSchema, DataFrameSchema, MultiIndexDataFrameSchema


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class SchemaError(ValueError):
    """Raised when a DataFrame does not conform to its schema."""


# ---------------------------------------------------------------------------
# Core validator — flat DataFrameSchema
# ---------------------------------------------------------------------------

def validate_dataframe(
    df: pd.DataFrame,
    schema: DataFrameSchema,
    *,
    raise_on_error: bool = True,
) -> list[str]:
    """Validate a flat DataFrame against a ``DataFrameSchema``.

    Returns a list of problem strings (empty = valid).
    Raises ``SchemaError`` on the first problem when ``raise_on_error=True``.
    """
    problems: list[str] = []

    def _fail(msg: str) -> None:
        if raise_on_error:
            raise SchemaError(f"[{schema.name}] {msg}")
        problems.append(msg)

    if len(df) < schema.min_rows:
        _fail(f"Expected at least {schema.min_rows} rows, got {len(df)}.")

    if schema.index_name is not None and df.index.name != schema.index_name:
        _fail(f"Index name should be '{schema.index_name}', got '{df.index.name}'.")

    if schema.index_dtype is not None:
        if not _dtype_compatible(str(df.index.dtype), schema.index_dtype):
            _fail(f"Index dtype should be '{schema.index_dtype}', got '{df.index.dtype}'.")

    for col_schema in schema.columns:
        if col_schema.name not in df.columns:
            _fail(f"Required column '{col_schema.name}' is missing.")
            continue
        _validate_column(df[col_schema.name], col_schema, schema.name, _fail)

    if schema.check_hourly_continuity and isinstance(df.index, pd.DatetimeIndex):
        expected_idx = pd.date_range(df.index.min(), df.index.max(), freq="h", tz=df.index.tz)
        missing = expected_idx.difference(df.index)
        if not missing.empty:
            _fail(
                f"DatetimeIndex has {len(missing)} missing hourly timestamp(s); "
                f"first gap at {missing[0]}."
            )

    if not schema.allow_extra_columns:
        expected_names = {c.name for c in schema.columns}
        extras = set(df.columns) - expected_names
        if extras:
            _fail(f"Unexpected columns: {sorted(extras)}.")

    return problems


# ---------------------------------------------------------------------------
# Core validator — MultiIndexDataFrameSchema  (actgen)
# ---------------------------------------------------------------------------

def validate_multiindex_dataframe(
    df: pd.DataFrame,
    schema: MultiIndexDataFrameSchema,
    *,
    raise_on_error: bool = True,
) -> list[str]:
    """Validate a MultiIndex-column DataFrame against a ``MultiIndexDataFrameSchema``.

    Checks:
    - Row count and hourly continuity on the index.
    - Columns is a MultiIndex with the expected level names.
    - All expected level-0 values (zones) are present.
    - At least one expected level-1 value (production type) is present per zone.
    - All values are numeric and within the declared bounds.
    """
    problems: list[str] = []

    def _fail(msg: str) -> None:
        if raise_on_error:
            raise SchemaError(f"[{schema.name}] {msg}")
        problems.append(msg)

    # --- row count -----------------------------------------------------------
    if len(df) < schema.min_rows:
        _fail(f"Expected at least {schema.min_rows} rows, got {len(df)}.")

    # --- index ---------------------------------------------------------------
    if schema.index_name is not None and df.index.name != schema.index_name:
        _fail(f"Index name should be '{schema.index_name}', got '{df.index.name}'.")

    if schema.index_dtype is not None:
        if not _dtype_compatible(str(df.index.dtype), schema.index_dtype):
            _fail(f"Index dtype should be '{schema.index_dtype}', got '{df.index.dtype}'.")

    # --- MultiIndex columns --------------------------------------------------
    if not isinstance(df.columns, pd.MultiIndex):
        _fail(
            f"Columns should be a MultiIndex (level 0 = zone, level 1 = type), "
            f"got {type(df.columns).__name__}."
        )
        return problems  # remaining checks require MultiIndex

    if schema.level_names:
        actual_names = list(df.columns.names)
        if actual_names != schema.level_names:
            _fail(
                f"Column level names should be {schema.level_names}, "
                f"got {actual_names}."
            )

    # --- expected zones (level 0) --------------------------------------------
    actual_level0 = set(df.columns.get_level_values(0).unique())
    for zone in schema.expected_level0:
        if zone not in actual_level0:
            _fail(f"Expected zone '{zone}' missing from column level 0.")

    # --- expected types (level 1) — at least one must appear per zone --------
    if schema.expected_level1:
        actual_level1 = set(df.columns.get_level_values(1).unique())
        found = actual_level1 & set(schema.expected_level1)
        if not found:
            _fail(
                f"None of the expected production types found in column level 1. "
                f"Expected at least one of: {sorted(schema.expected_level1)}."
            )

    # --- value bounds --------------------------------------------------------
    values = df.values
    import numpy as np
    numeric_values = pd.to_numeric(df.values.ravel(), errors="coerce")

    if schema.min_value is not None:
        n_below = int((numeric_values < schema.min_value).sum())
        if n_below:
            _fail(
                f"{n_below} value(s) below minimum {schema.min_value} {schema.unit}."
            )

    if schema.max_value is not None:
        n_above = int((numeric_values > schema.max_value).sum())
        if n_above:
            _fail(
                f"{n_above} value(s) above maximum {schema.max_value} {schema.unit}."
            )

    # --- hourly continuity ---------------------------------------------------
    if schema.check_hourly_continuity and isinstance(df.index, pd.DatetimeIndex):
        expected_idx = pd.date_range(df.index.min(), df.index.max(), freq="h", tz=df.index.tz)
        missing = expected_idx.difference(df.index)
        if not missing.empty:
            _fail(
                f"DatetimeIndex has {len(missing)} missing hourly timestamp(s); "
                f"first gap at {missing[0]}."
            )

    return problems


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def validate_prices(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate a prices DataFrame against ``PRICES_SCHEMA``."""
    from .schemas import PRICES_SCHEMA
    problems = validate_dataframe(df, PRICES_SCHEMA, raise_on_error=raise_on_error)
    for col in df.columns:
        series = df[col]
        if not pd.api.types.is_float_dtype(series) and not pd.api.types.is_integer_dtype(series):
            msg = f"Column '{col}' should be numeric (float64), got dtype '{series.dtype}'."
            if raise_on_error:
                raise SchemaError(f"[prices] {msg}")
            problems.append(msg)
    return problems


def validate_load(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate a load DataFrame against ``LOAD_SCHEMA``.

    Also checks that all columns are numeric — a non-numeric column usually
    means ENTSO-E ``"-"`` sentinels were not coerced to NaN during ingestion.
    """
    from .schemas import LOAD_SCHEMA
    problems = validate_dataframe(df, LOAD_SCHEMA, raise_on_error=raise_on_error)
    for col in df.columns:
        series = df[col]
        if not pd.api.types.is_float_dtype(series) and not pd.api.types.is_integer_dtype(series):
            msg = (
                f"Column '{col}' should be numeric (float64 MW), got dtype '{series.dtype}'. "
                f"Check that ENTSO-E '-' sentinels were coerced to NaN during ingestion."
            )
            if raise_on_error:
                raise SchemaError(f"[load] {msg}")
            problems.append(msg)
    return problems


def validate_gen(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate an actgen DataFrame against ``GEN_SCHEMA``.

    Expects a MultiIndex column DataFrame as produced by ``load_actgen_table``.
    Passing a raw tall-format CSV will fail because the columns won't be a
    MultiIndex.
    """
    from .schemas import GEN_SCHEMA
    return validate_multiindex_dataframe(df, GEN_SCHEMA, raise_on_error=raise_on_error)


def validate_transmission(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate an internal transmission DataFrame against ``TRANSMISSION_INTERNAL_SCHEMA``.

    Expects the net-flow wide format produced by ``load_transmission_table``:
    columns = "NOx-NOy" strings, values can be negative.
    """
    from .schemas import TRANSMISSION_INTERNAL_SCHEMA
    problems = validate_dataframe(df, TRANSMISSION_INTERNAL_SCHEMA, raise_on_error=raise_on_error)
    for col in df.columns:
        series = df[col]
        if not pd.api.types.is_float_dtype(series) and not pd.api.types.is_integer_dtype(series):
            msg = (
                f"Column '{col}' should be numeric (float64 MW), got dtype '{series.dtype}'. "
                f"Check that ENTSO-E 'n/e' sentinels were coerced to NaN during ingestion."
            )
            if raise_on_error:
                raise SchemaError(f"[transmission] {msg}")
            problems.append(msg)
    return problems


def validate_external_balance(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate an external balance DataFrame against ``EXTERNAL_BALANCE_SCHEMA``.

    Expects the per-zone net import format produced by ``load_transmission_table``:
    columns = zone names NO1-NO5, values can be negative (exporting).
    """
    from .schemas import EXTERNAL_BALANCE_SCHEMA
    problems = validate_dataframe(df, EXTERNAL_BALANCE_SCHEMA, raise_on_error=raise_on_error)
    for col in df.columns:
        series = df[col]
        if not pd.api.types.is_float_dtype(series) and not pd.api.types.is_integer_dtype(series):
            msg = f"Column '{col}' should be numeric (float64 MW), got dtype '{series.dtype}'."
            if raise_on_error:
                raise SchemaError(f"[external_balance] {msg}")
            problems.append(msg)
    return problems


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_column(
    series: pd.Series,
    col_schema: ColumnSchema,
    schema_name: str,
    fail_fn: Any,
) -> None:
    if not col_schema.nullable and series.isna().any():
        n_null = int(series.isna().sum())
        fail_fn(f"Column '{col_schema.name}' must not contain nulls ({n_null} found).")

    actual_dtype = str(series.dtype)
    if not _dtype_compatible(actual_dtype, col_schema.dtype):
        fail_fn(
            f"Column '{col_schema.name}' dtype should be "
            f"'{col_schema.dtype}', got '{actual_dtype}'."
        )

    if col_schema.min_value is not None:
        below = series.dropna()
        below = below[below < col_schema.min_value]
        if not below.empty:
            unit_hint = f" {col_schema.unit}" if col_schema.unit else ""
            fail_fn(
                f"Column '{col_schema.name}' has {len(below)} value(s) "
                f"below minimum {col_schema.min_value}{unit_hint} "
                f"(e.g. {below.iloc[0]:.4g})."
            )

    if col_schema.max_value is not None:
        above = series.dropna()
        above = above[above > col_schema.max_value]
        if not above.empty:
            unit_hint = f" {col_schema.unit}" if col_schema.unit else ""
            fail_fn(
                f"Column '{col_schema.name}' has {len(above)} value(s) "
                f"above maximum {col_schema.max_value}{unit_hint} "
                f"(e.g. {above.iloc[0]:.4g})."
            )


def _dtype_compatible(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    if expected == "object" and actual in ("str", "string", "object"):
        return True
    if actual.startswith("datetime64") and expected.startswith("datetime64"):
        def _tz(s: str) -> str:
            parts = s.split(",")
            return parts[-1].strip().rstrip("]") if len(parts) > 1 else ""
        return _tz(actual) == _tz(expected)
    if expected == "float64":
        return pd.api.types.is_float_dtype(actual) or pd.api.types.is_integer_dtype(actual)
    return False
