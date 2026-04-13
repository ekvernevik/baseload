"""Data schemas for the Baseload Phase 1 MVP pipeline.

Each schema defines the expected columns, dtypes, units, and constraints for a
pipeline artifact. Schemas are intentionally lightweight dataclasses so they can
be imported anywhere without heavy dependencies.

Data streams
------------
prices       : Day-ahead spot prices (EUR/MWh), all five NO zones, hourly UTC.
               Source: ENTSO-E — Day-ahead prices.
               Raw files : NO{1-5}_prices_2026.csv  (15-min, aggregated to 1h on ingest).
               Processed : data/processed/prices.parquet  (wide, columns = zone names).

load         : Actual total load (MW), all five NO zones, hourly UTC.
               Source: ENTSO-E — Actual Total Load.
               Raw files : NO{1-5}_load_2025.csv  (already 1h resolution).
               Processed : data/processed/load.parquet  (wide, columns = zone names).

gen          : Actual generation per production type (MW), hourly UTC.
               Source: ENTSO-E — Actual Generation per Production Type.
               Raw files : NO{1-5}_gen_2025.csv  (tall: one row per hour per type).
               Processed : data/processed/gen_{zone}.parquet per zone
                           (wide, columns = ENTSO-E production type names).
               Note: only 9 of the 21 ENTSO-E types are ever non-zero in Norway;
               the rest are allowed as extra columns but not schema-required.

transmission : Cross-border physical flows (MW), hourly UTC.
               Source: ENTSO-E — Cross-Border Physical Flows.
               Raw files : NO{1-5}_transmission_2025.csv  (tall: one row per hour per pair).
               Processed : data/processed/transmission.parquet  (single combined wide file,
                           columns = "OutArea>InArea" border pair strings, duplicates removed).

Artifact classification
-----------------------
Intermediate  data/processed/*.parquet  — consumed by downstream scripts only.
Final         artifacts/tables/*.parquet + *.csv — delivered to end-users / reports.
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
    dtype: str                         # pandas dtype string, e.g. "float64", "datetime64[ns, UTC]"
    nullable: bool = False             # whether NaN is allowed
    min_value: float | None = None     # inclusive lower bound (numeric columns)
    max_value: float | None = None     # inclusive upper bound (numeric columns)
    unit: str = ""                     # physical unit for documentation, e.g. "EUR/MWh", "MW"
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
    min_rows: int = 24                               # minimum number of rows expected
    check_hourly_continuity: bool = False            # if True, validate no gaps in hourly DatetimeIndex


# ---------------------------------------------------------------------------
# Intermediate artifact schemas  (data/processed/)
# ---------------------------------------------------------------------------

#: Hourly UTC day-ahead price table produced by ingest_entsoe.py.
#: Index: DatetimeTZDtype("h", tz="UTC"), columns = zone names (float64 EUR/MWh).
#: Norwegian prices can go negative (high hydro / wind export scenarios; NO4 reached
#: −27 EUR/MWh in 2026).  Upper bound is generous to cover crisis-period spikes.
PRICES_SCHEMA = DataFrameSchema(
    name="prices",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        ColumnSchema("NO1", dtype="float64", nullable=True, min_value=-500.0, max_value=3000.0, unit="EUR/MWh"),
        ColumnSchema("NO2", dtype="float64", nullable=True, min_value=-500.0, max_value=3000.0, unit="EUR/MWh"),
        ColumnSchema("NO3", dtype="float64", nullable=True, min_value=-500.0, max_value=3000.0, unit="EUR/MWh"),
        ColumnSchema("NO4", dtype="float64", nullable=True, min_value=-500.0, max_value=3000.0, unit="EUR/MWh"),
        ColumnSchema("NO5", dtype="float64", nullable=True, min_value=-500.0, max_value=3000.0, unit="EUR/MWh"),
    ],
    allow_extra_columns=True,
    min_rows=168,                   # at least one week of hourly data
    check_hourly_continuity=True,
)

#: Hourly UTC actual total load table produced by ingest_entsoe.py.
#: Index: DatetimeTZDtype("h", tz="UTC"), columns = zone names (float64 MW).
#: Observed 2025 range: NO1 1836–7169, NO2 2937–6052, NO3 2252–4667,
#:                       NO4 1442–3370, NO5 953–3147 MW.  Upper bound is 2× observed max.
LOAD_SCHEMA = DataFrameSchema(
    name="load",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        ColumnSchema("NO1", dtype="float64", nullable=True, min_value=0.0, max_value=15000.0, unit="MW"),
        ColumnSchema("NO2", dtype="float64", nullable=True, min_value=0.0, max_value=15000.0, unit="MW"),
        ColumnSchema("NO3", dtype="float64", nullable=True, min_value=0.0, max_value=15000.0, unit="MW"),
        ColumnSchema("NO4", dtype="float64", nullable=True, min_value=0.0, max_value=15000.0, unit="MW"),
        ColumnSchema("NO5", dtype="float64", nullable=True, min_value=0.0, max_value=15000.0, unit="MW"),
    ],
    allow_extra_columns=True,
    min_rows=168,
    check_hourly_continuity=True,
)

#: Hourly UTC generation per production type, one processed file per zone.
#: Columns are the raw ENTSO-E "Production Type" strings (kept as-is after pivot).
#: Only the 9 types that are ever non-zero in any Norwegian zone are required;
#: all are nullable because different zones have different active types.
#: Observed 2025 maxima (across all zones):
#:   Hydro Water Reservoir ~1810 MW, Hydro RoR ~1260 MW,
#:   Wind Onshore ~380 MW, Hydro Pumped Storage ~80 MW, etc.
#: Upper bounds set at 2-3× installed capacity for each category.
GEN_SCHEMA = DataFrameSchema(
    name="gen",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        ColumnSchema("Hydro Water Reservoir",          dtype="float64", nullable=True, min_value=0.0, max_value=20000.0, unit="MW"),
        ColumnSchema("Hydro Run-of-river and pondage", dtype="float64", nullable=True, min_value=0.0, max_value=20000.0, unit="MW"),
        ColumnSchema("Hydro Pumped Storage",           dtype="float64", nullable=True, min_value=0.0, max_value=5000.0,  unit="MW"),
        ColumnSchema("Wind Onshore",                   dtype="float64", nullable=True, min_value=0.0, max_value=10000.0, unit="MW"),
        ColumnSchema("Wind Offshore",                  dtype="float64", nullable=True, min_value=0.0, max_value=5000.0,  unit="MW"),
        ColumnSchema("Solar",                          dtype="float64", nullable=True, min_value=0.0, max_value=2000.0,  unit="MW"),
        ColumnSchema("Fossil Gas",                     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0,  unit="MW"),
        ColumnSchema("Waste",                          dtype="float64", nullable=True, min_value=0.0, max_value=2000.0,  unit="MW"),
        ColumnSchema("Other renewable",                dtype="float64", nullable=True, min_value=0.0, max_value=5000.0,  unit="MW"),
    ],
    allow_extra_columns=True,   # zero-generation types present in raw files are allowed
    min_rows=168,
    check_hourly_continuity=True,
)

#: Hourly UTC cross-border physical flows, single combined file for all zones.
#: Columns are "OutArea>InArea" strings, e.g. "NO1>NO2".
#: Physical flows are non-negative (direction is encoded in the column name).
#: 30 active border pairs observed in 2025 data; all nullable because flow is
#: zero on some hours.  Upper bound 5000 MW covers all Norwegian interconnectors.
#: Includes flows to/from neighbouring countries: SE, DK, DE-LU, NL, GB, FI.
TRANSMISSION_SCHEMA = DataFrameSchema(
    name="transmission",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        # --- Internal Norwegian flows ---
        ColumnSchema("NO1>NO2",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO2>NO1",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO1>NO3",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO3>NO1",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO1>NO5",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO5>NO1",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO2>NO5",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO5>NO2",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO3>NO4",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO4>NO3",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO3>NO5",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO5>NO3",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        # --- NO ↔ Sweden ---
        ColumnSchema("NO1>SE3",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("SE3>NO1",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO3>SE2",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("SE2>NO3",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO4>SE1",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("SE1>NO4",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO4>SE2",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("SE2>NO4",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        # --- NO ↔ Continental Europe / UK ---
        ColumnSchema("NO2>DE-LU",  dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("DE-LU>NO2",  dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO2>DK1",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("DK1>NO2",    dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO2>GB",     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("GB>NO2",     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO2>NL",     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NL>NO2",     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        # --- NO ↔ Finland ---
        ColumnSchema("NO4>FI",     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
        ColumnSchema("FI>NO4",     dtype="float64", nullable=True, min_value=0.0, max_value=5000.0, unit="MW"),
    ],
    allow_extra_columns=True,   # n/e pairs (NO1A) and any future additions are allowed
    min_rows=168,
    check_hourly_continuity=True,
)


# ---------------------------------------------------------------------------
# Final artifact schemas  (artifacts/tables/)
# ---------------------------------------------------------------------------

#: Perfect-foresight valuation table written by bess_valuation_pf.py.
VALUATION_PF_SCHEMA = DataFrameSchema(
    name="valuation_pf",
    columns=[
        ColumnSchema("zone",          dtype="object",  nullable=True),
        ColumnSchema("net_revenue",   dtype="float64", nullable=True, unit="EUR", description="Total revenue over period"),
        ColumnSchema("eur_per_kw_yr", dtype="float64", nullable=True, min_value=0.0, unit="EUR/kW/yr"),
    ],
    allow_extra_columns=True,
    min_rows=1,
)

#: Rolling-horizon valuation table written by bess_valuation_rh.py.
VALUATION_RH_SCHEMA = DataFrameSchema(
    name="valuation_rh",
    columns=[
        ColumnSchema("zone",             dtype="object",  nullable=True),
        ColumnSchema("rolling_revenue",  dtype="float64", nullable=True, unit="EUR"),
        ColumnSchema("pf_revenue",       dtype="float64", nullable=True, unit="EUR"),
        ColumnSchema("penalty_pct",      dtype="float64", nullable=True, unit="%"),
        ColumnSchema("eur_per_kw_yr",    dtype="float64", nullable=True, min_value=0.0, unit="EUR/kW/yr"),
    ],
    allow_extra_columns=True,
    min_rows=1,
)


# ---------------------------------------------------------------------------
# Registry – maps artifact stem → schema, used by io.py at read/write time
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: dict[str, DataFrameSchema] = {
    # intermediate
    "prices":        PRICES_SCHEMA,
    "load":          LOAD_SCHEMA,
    "gen":           GEN_SCHEMA,
    "transmission":  TRANSMISSION_SCHEMA,
    # final
    "valuation_pf":  VALUATION_PF_SCHEMA,
    "valuation_rh":  VALUATION_RH_SCHEMA,
}
