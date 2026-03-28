
"""Data schemas for the Baseload Phase 1 MVP pipeline.
 
Each schema defines the expected columns, dtypes, and constraints for a
pipeline artifact. Schemas are intentionally lightweight dataclasses so
they can be imported anywhere without heavy dependencies.
"""
from __future__ import annotations
 
from dataclasses import dataclass, field
from typing import Any
 
 
# ---------------------------------------------------------------------------
# Column descriptor
# ---------------------------------------------------------------------------
 
@dataclass
class ColumnSchema:
    """Describes a single column in a DataFrame schema."""
    name: str
    dtype: str                        # pandas dtype string, e.g. "float64", "datetime64[ns, UTC]"
    nullable: bool = False            # whether NaN is allowed
    min_value: float | None = None    # inclusive lower bound (numeric columns)
    max_value: float | None = None    # inclusive upper bound (numeric columns)
    description: str = ""
 
 
# ---------------------------------------------------------------------------
# DataFrame schema
# ---------------------------------------------------------------------------
 
@dataclass
class DataFrameSchema:
    """Describes the expected structure of a DataFrame artifact."""
    name: str                                        # human-readable label used in error messages
    index_name: str | None = None                    # expected name of the index (None = don't check)
    index_dtype: str | None = None                   # expected dtype of the index
    columns: list[ColumnSchema] = field(default_factory=list)
    allow_extra_columns: bool = True                 # if False, unknown columns raise an error
    min_rows: int = 24                               # at least 24 rows expected (e.g. hourly data for 1 day)
 
 
# ---------------------------------------------------------------------------
# Canonical schemas
# ---------------------------------------------------------------------------
 
#: Hourly UTC price / load / generation table produced by ingest_entsoe.py
#: Index: DatetimeTZDtype("h", tz="UTC"), columns = zone names (float64)
PRICES_SCHEMA = DataFrameSchema(
    name="prices",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        ColumnSchema("NO1", dtype="float64", nullable=True, min_value=0.0, max_value=20000.0),
        ColumnSchema("NO2", dtype="float64", nullable=True, min_value=0.0, max_value=30000.0),
        ColumnSchema("NO3", dtype="float64", nullable=True, min_value=0.0, max_value=20000.0),
        ColumnSchema("NO4", dtype="float64", nullable=True, min_value=0.0, max_value=10000.0),
        ColumnSchema("NO5", dtype="float64", nullable=True, min_value=0.0, max_value=15000.0),
    ],           # zone columns are dynamic; validators check dtype per-column
    allow_extra_columns=True,
    min_rows=1,
)
 
#: Perfect-foresight valuation table written by bess_valuation_pf.py
VALUATION_PF_SCHEMA = DataFrameSchema(
    name="valuation_pf",
    columns=[
        ColumnSchema("zone",          dtype="object",  nullable=True),
        ColumnSchema("net_revenue",   dtype="float64", nullable=True, description="EUR total revenue"),
        ColumnSchema("eur_per_kw_yr", dtype="float64", nullable=True, min_value=0.0),
    ],
    allow_extra_columns=True,
    min_rows=1,
)
 
#: Rolling-horizon valuation table written by bess_valuation_rh.py
VALUATION_RH_SCHEMA = DataFrameSchema(
    name="valuation_rh",
    columns=[
        ColumnSchema("zone",             dtype="object",  nullable=True),
        ColumnSchema("rolling_revenue",  dtype="float64", nullable=True),
        ColumnSchema("pf_revenue",       dtype="float64", nullable=True),
        ColumnSchema("penalty_pct",      dtype="float64", nullable=True),
        ColumnSchema("eur_per_kw_yr",    dtype="float64", nullable=True, min_value=0.0),
    ],
    allow_extra_columns=True,
    min_rows=1,
)
 
#: Registry – maps artifact stem → schema for use by io.py
SCHEMA_REGISTRY: dict[str, DataFrameSchema] = {
    "prices":        PRICES_SCHEMA,
    "valuation_pf":  VALUATION_PF_SCHEMA,
    "valuation_rh":  VALUATION_RH_SCHEMA,
}