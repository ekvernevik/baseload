"""Data schemas for the Baseload Phase 1 MVP pipeline.

Each schema defines the expected columns, dtypes, units, and constraints for a
pipeline artifact.  Schemas are intentionally lightweight dataclasses so they
can be imported anywhere without heavy dependencies.

Data streams and processed file layout
---------------------------------------
prices            data/processed/prices.parquet
                  Wide, flat: columns = zone names (NO1-NO5), values = EUR/MWh.

load              data/processed/load.parquet
                  Wide, flat: columns = zone names (NO1-NO5), values = MW.

actgen            data/processed/actgen.parquet
                  Wide, MultiIndex columns: level 0 = zone, level 1 = ENTSO-E
                  production type name.  Values = MW.
                  Level names: ["_zone", "_type"]  (set by pivot_table in
                  load_actgen_table).

transmission      data/processed/transmission.parquet
                  Wide, flat: columns = "NOx-NOy" net-flow pair strings
                  (lower-numbered zone first).  Values = MW net flow; positive
                  means flow toward the higher-numbered zone, negative the reverse.

external_balance  data/processed/external_balance.parquet
                  Wide, flat: columns = zone names (NO1-NO5).
                  Values = MW net external import; positive = importing,
                  negative = exporting.

mfrr_capacity     data/processed/mfrr_capacity.parquet
mfrr_activation   data/processed/mfrr_activation.parquet
afrr_capacity     data/processed/afrr_capacity.parquet
afrr_activation   data/processed/afrr_activation.parquet
                  Wide, flat: columns = "{zone}_{direction}_price" (EUR/MWh)
                  and "{zone}_{direction}_volume" (MW), direction in
                  {up, down}. Not every zone/direction trades — missing
                  combinations are NaN. Activation volume is the average
                  activated MW over the settlement period, as ENTSO-E
                  publishes it, not cumulative MWh.

Final artifacts   artifacts/tables/
                  valuation_pf, valuation_rh.

Artifact classification
-----------------------
Intermediate  data/processed/*.parquet  — consumed by downstream scripts only.
Final         artifacts/tables/*.parquet + *.csv — delivered to reports / end-users.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Column descriptor  (used in flat DataFrameSchema)
# ---------------------------------------------------------------------------

@dataclass
class ColumnSchema:
    """Describes a single column in a flat DataFrame."""
    name: str
    dtype: str                         # pandas dtype string, e.g. "float64"
    nullable: bool = False
    min_value: float | None = None     # inclusive lower bound
    max_value: float | None = None     # inclusive upper bound
    unit: str = ""                     # e.g. "EUR/MWh", "MW"
    description: str = ""


# ---------------------------------------------------------------------------
# Flat DataFrame schema
# ---------------------------------------------------------------------------

@dataclass
class DataFrameSchema:
    """Describes the expected structure of a flat (single-level columns) DataFrame."""
    name: str
    index_name: str | None = None      # None = don't check
    index_dtype: str | None = None
    columns: list[ColumnSchema] = field(default_factory=list)
    allow_extra_columns: bool = True
    min_rows: int = 24
    check_hourly_continuity: bool = False


# ---------------------------------------------------------------------------
# MultiIndex DataFrame schema  (used for actgen)
# ---------------------------------------------------------------------------

@dataclass
class MultiIndexDataFrameSchema:
    """Describes a DataFrame whose columns form a two-level MultiIndex.

    Produced by ``load_actgen_table`` via ``pivot_table(columns=["_zone","_type"])``.
    Level 0 = zone names, level 1 = ENTSO-E production type names.
    """
    name: str
    index_name: str | None = None      # None = don't check (reindex loses the name)
    index_dtype: str | None = None
    min_rows: int = 24
    check_hourly_continuity: bool = False
    expected_level0: list[str] = field(default_factory=list)   # expected zone names
    expected_level1: list[str] = field(default_factory=list)   # expected type names (subset)
    level_names: list[str] = field(default_factory=list)       # e.g. ["_zone", "_type"]
    min_value: float | None = None
    max_value: float | None = None
    unit: str = ""


# ---------------------------------------------------------------------------
# Intermediate artifact schemas  (data/processed/)
# ---------------------------------------------------------------------------

#: Day-ahead spot prices, hourly UTC.
#: Observed 2026 range: NO4 reached -27 EUR/MWh; upper bound covers crisis spikes.
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
    min_rows=168,
    check_hourly_continuity=True,
)

#: Actual total load, hourly UTC.
#: Observed 2025 maxima: NO1 7169, NO2 6052, NO3 4667, NO4 3370, NO5 3147 MW.
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

#: Actual generation per production type, hourly UTC.
#: MultiIndex columns: level 0 = zone (NO1-NO5), level 1 = ENTSO-E type name.
#: Level names = ["_zone", "_type"] as set by pivot_table in load_actgen_table.
#: Not every (zone, type) combination exists — inactive pairs are simply absent.
#: Generation is always non-negative; upper bound 20 000 MW covers total NO hydro capacity.
GEN_SCHEMA = MultiIndexDataFrameSchema(
    name="actgen",
    index_name=None,            # pivot_table sets "_time"; reindex(idx) clears it
    index_dtype="datetime64[ns, UTC]",
    min_rows=168,
    check_hourly_continuity=True,
    expected_level0=["NO1", "NO2", "NO3", "NO4", "NO5"],
    expected_level1=[
        "Hydro Water Reservoir",
        "Hydro Run-of-river and pondage",
        "Hydro Pumped Storage",
        "Wind Onshore",
        "Wind Offshore",
        "Solar",
        "Fossil Gas",
        "Waste",
        "Other renewable",
    ],
    level_names=["_zone", "_type"],
    min_value=0.0,
    max_value=20000.0,
    unit="MW",
)

#: Internal NO-NO net physical flows, hourly UTC.
#: Columns: "NOx-NOy" where x < y (lower zone number first).
#: Values: net MW toward higher-numbered zone; negative = reverse direction.
#: 6 internal pairs observed in 2025 data.
TRANSMISSION_INTERNAL_SCHEMA = DataFrameSchema(
    name="transmission",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        ColumnSchema("NO1-NO2", dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO1-NO3", dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO1-NO5", dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO2-NO5", dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO3-NO4", dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW"),
        ColumnSchema("NO3-NO5", dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW"),
    ],
    allow_extra_columns=True,
    min_rows=168,
    check_hourly_continuity=True,
)

#: Net external import balance per NO zone, hourly UTC.
#: Columns: zone names NO1-NO5.
#: Values: net MW import from outside Norway; positive = importing, negative = exporting.
#: Includes all non-NO neighbours: SE, DK, DE-LU, NL, GB, FI.
EXTERNAL_BALANCE_SCHEMA = DataFrameSchema(
    name="external_balance",
    index_name="time",
    index_dtype="datetime64[ns, UTC]",
    columns=[
        ColumnSchema("NO1", dtype="float64", nullable=True, min_value=-10000.0, max_value=10000.0, unit="MW"),
        ColumnSchema("NO2", dtype="float64", nullable=True, min_value=-10000.0, max_value=10000.0, unit="MW"),
        ColumnSchema("NO3", dtype="float64", nullable=True, min_value=-10000.0, max_value=10000.0, unit="MW"),
        ColumnSchema("NO4", dtype="float64", nullable=True, min_value=-10000.0, max_value=10000.0, unit="MW"),
        ColumnSchema("NO5", dtype="float64", nullable=True, min_value=-10000.0, max_value=10000.0, unit="MW"),
    ],
    allow_extra_columns=False,
    min_rows=168,
    check_hourly_continuity=True,
)


#: Reserve-market (mFRR EAM / aFRR) capacity or activation series, hourly UTC.
#: Columns per zone: "{zone}_up_price", "{zone}_up_volume",
#: "{zone}_down_price", "{zone}_down_volume". All nullable — not every zone
#: trades every direction. Price bounds are wider than day-ahead (-1000..5000
#: EUR/MWh) since reserve/activation prices spike harder during scarcity.
def build_reserve_schema(name: str, zones: list[str]) -> DataFrameSchema:
    columns = []
    for zone in zones:
        for direction in ("up", "down"):
            columns.append(
                ColumnSchema(
                    f"{zone}_{direction}_price", dtype="float64", nullable=True,
                    min_value=-1000.0, max_value=5000.0, unit="EUR/MWh",
                )
            )
            columns.append(
                ColumnSchema(
                    f"{zone}_{direction}_volume", dtype="float64", nullable=True,
                    min_value=0.0, max_value=2000.0, unit="MW",
                )
            )
    return DataFrameSchema(
        name=name,
        index_name="time",
        index_dtype="datetime64[ns, UTC]",
        columns=columns,
        allow_extra_columns=True,
        min_rows=168,
        check_hourly_continuity=True,
    )


# ---------------------------------------------------------------------------
# Final artifact schemas  (artifacts/tables/)
# ---------------------------------------------------------------------------

VALUATION_PF_SCHEMA = DataFrameSchema(
    name="valuation_pf",
    columns=[
        ColumnSchema("zone",          dtype="object",  nullable=True),
        ColumnSchema("net_revenue",   dtype="float64", nullable=True, unit="EUR"),
        ColumnSchema("eur_per_kw_yr", dtype="float64", nullable=True, min_value=0.0, unit="EUR/kW/yr"),
    ],
    allow_extra_columns=True,
    min_rows=1,
)

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
# Registry
# ---------------------------------------------------------------------------

MFRR_CAPACITY_SCHEMA = build_reserve_schema("mfrr_capacity", ["NO1", "NO2", "NO3", "NO4", "NO5"])
MFRR_ACTIVATION_SCHEMA = build_reserve_schema("mfrr_activation", ["NO1", "NO2", "NO3", "NO4", "NO5"])
AFRR_CAPACITY_SCHEMA = build_reserve_schema("afrr_capacity", ["NO1", "NO2", "NO3", "NO4", "NO5"])
AFRR_ACTIVATION_SCHEMA = build_reserve_schema("afrr_activation", ["NO1", "NO2", "NO3", "NO4", "NO5"])


SCHEMA_REGISTRY: dict[str, DataFrameSchema | MultiIndexDataFrameSchema] = {
    # intermediate
    "prices":            PRICES_SCHEMA,
    "load":              LOAD_SCHEMA,
    "actgen":            GEN_SCHEMA,
    "transmission":      TRANSMISSION_INTERNAL_SCHEMA,
    "external_balance":  EXTERNAL_BALANCE_SCHEMA,
    "mfrr_capacity":     MFRR_CAPACITY_SCHEMA,
    "mfrr_activation":   MFRR_ACTIVATION_SCHEMA,
    "afrr_capacity":     AFRR_CAPACITY_SCHEMA,
    "afrr_activation":   AFRR_ACTIVATION_SCHEMA,
    # final
    "valuation_pf":      VALUATION_PF_SCHEMA,
    "valuation_rh":      VALUATION_RH_SCHEMA,
}
