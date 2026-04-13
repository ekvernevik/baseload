"""Validation helpers for the Baseload Phase 1 MVP pipeline.

All public functions raise ``SchemaError`` (a subclass of ``ValueError``)
on the *first* problem found so failures surface early and messages are
actionable.  Pass ``raise_on_error=False`` to collect all problems instead.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .schemas import ColumnSchema, DataFrameSchema


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class SchemaError(ValueError):
    """Raised when a DataFrame does not conform to its schema."""


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def validate_dataframe(
    df: pd.DataFrame,
    schema: DataFrameSchema,
    *,
    raise_on_error: bool = True,
) -> list[str]:
    """Validate *df* against *schema*.

    Parameters
    ----------
    df:
        The DataFrame to validate.
    schema:
        The ``DataFrameSchema`` to validate against.
    raise_on_error:
        If ``True`` (default) raise ``SchemaError`` on the first problem.
        If ``False`` collect all problems and return them as a list of
        strings.  An empty list means the DataFrame is valid.

    Returns
    -------
    list[str]
        Problems found (only meaningful when ``raise_on_error=False``).
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
        _fail(
            f"Index name should be '{schema.index_name}', "
            f"got '{df.index.name}'."
        )

    if schema.index_dtype is not None:
        idx_dtype = str(df.index.dtype)
        # datetime64[ns, UTC] may appear as "datetime64[ns, UTC]" or
        # "datetime64[us, UTC]" depending on pandas version – check prefix
        expected = schema.index_dtype
        if not _dtype_compatible(idx_dtype, expected):
            _fail(
                f"Index dtype should be '{expected}', got '{idx_dtype}'."
            )

    # --- required columns ----------------------------------------------------
    for col_schema in schema.columns:
        if col_schema.name not in df.columns:
            _fail(f"Required column '{col_schema.name}' is missing.")
            continue  # skip further checks for this column if not raising

        _validate_column(df[col_schema.name], col_schema, schema.name, _fail)

    # --- hourly continuity ---------------------------------------------------
    if schema.check_hourly_continuity and isinstance(df.index, pd.DatetimeIndex):
        expected_idx = pd.date_range(df.index.min(), df.index.max(), freq="h", tz=df.index.tz)
        missing = expected_idx.difference(df.index)
        if not missing.empty:
            _fail(
                f"DatetimeIndex has {len(missing)} missing hourly timestamp(s); "
                f"first gap at {missing[0]}."
            )

    # --- extra columns -------------------------------------------------------
    if not schema.allow_extra_columns:
        expected_names = {c.name for c in schema.columns}
        extras = set(df.columns) - expected_names
        if extras:
            _fail(f"Unexpected columns: {sorted(extras)}.")

    return problems


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def validate_prices(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate a prices DataFrame.

    Checks ``PRICES_SCHEMA`` (UTC datetime index, per-zone EUR/MWh bounds,
    hourly continuity) and additionally verifies that every data column is
    numeric — a non-numeric column usually means a raw CSV was passed in before
    ``standardize_series`` was run.
    """
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
    """Validate a load DataFrame.

    Checks ``LOAD_SCHEMA`` (UTC datetime index, per-zone MW bounds, hourly
    continuity) and additionally verifies that every data column is numeric.
    A non-numeric column usually means the ENTSO-E ``"-"`` sentinel was not
    coerced to NaN during ingestion.
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
    """Validate a per-zone generation DataFrame.

    Checks ``GEN_SCHEMA`` (UTC datetime index, production type column bounds,
    hourly continuity).  The DataFrame should already be pivoted to wide format
    (columns = ENTSO-E production type names) before calling this function.
    Raw tall-format CSVs will fail because the required columns won't exist.

    All generation values must be non-negative; negative values indicate a
    pivot or unit error in the ingestion step.
    """
    from .schemas import GEN_SCHEMA

    problems = validate_dataframe(df, GEN_SCHEMA, raise_on_error=raise_on_error)

    for col in df.columns:
        series = df[col]
        if not pd.api.types.is_float_dtype(series) and not pd.api.types.is_integer_dtype(series):
            msg = (
                f"Column '{col}' should be numeric (float64 MW), got dtype '{series.dtype}'. "
                f"Ensure the raw CSV was pivoted on 'Production Type' before validation."
            )
            if raise_on_error:
                raise SchemaError(f"[gen] {msg}")
            problems.append(msg)

    return problems


def validate_transmission(df: pd.DataFrame, *, raise_on_error: bool = True) -> list[str]:
    """Validate the combined transmission DataFrame.

    Checks ``TRANSMISSION_SCHEMA`` (UTC datetime index, border pair column
    bounds, hourly continuity).  The DataFrame should already be pivoted to
    wide format (columns = "OutArea>InArea" strings) before calling this
    function.

    Physical flows are non-negative; negative values indicate a pivot or sign
    error in the ingestion step.  ``n/e`` (not estimated) values in the raw
    ENTSO-E export must be coerced to NaN before validation.
    """
    from .schemas import TRANSMISSION_SCHEMA

    problems = validate_dataframe(df, TRANSMISSION_SCHEMA, raise_on_error=raise_on_error)

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_column(
    series: pd.Series,
    col_schema: ColumnSchema,
    schema_name: str,
    fail_fn: Any,
) -> None:
    """Run all per-column checks and call *fail_fn* for each problem."""
    # nullable check
    if not col_schema.nullable and series.isna().any():
        n_null = int(series.isna().sum())
        fail_fn(
            f"Column '{col_schema.name}' must not contain nulls "
            f"({n_null} found)."
        )

    # dtype check
    actual_dtype = str(series.dtype)
    if not _dtype_compatible(actual_dtype, col_schema.dtype):
        fail_fn(
            f"Column '{col_schema.name}' dtype should be "
            f"'{col_schema.dtype}', got '{actual_dtype}'."
        )

    # range checks
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
    """Return True if *actual* dtype string is compatible with *expected*.

    Uses prefix matching so that ``datetime64[us, UTC]`` is accepted when
    ``datetime64[ns, UTC]`` is expected (pandas 2.x changed the resolution).
    """
    if actual == expected:
        return True
    # pandas 2.x may report string columns as 'str' instead of 'object'
    if expected == "object" and actual in ("str", "string", "object"):
        return True
    # both datetime-with-tz: relax resolution differences
    if actual.startswith("datetime64") and expected.startswith("datetime64"):
        def _tz(s: str) -> str:
            parts = s.split(",")
            return parts[-1].strip().rstrip("]") if len(parts) > 1 else ""
        return _tz(actual) == _tz(expected)
    # float32 / float64 / Float64 are all "float-like"
    if expected == "float64":
        return pd.api.types.is_float_dtype(actual) or pd.api.types.is_integer_dtype(actual)
    return False
