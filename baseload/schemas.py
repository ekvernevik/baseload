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

Final artifacts   artifacts/tables/
                  valuation_pf, valuation_rh.

Artifact classification
-----------------------
Intermediate  data/processed/*.parquet  — consumed by downstream scripts only.
Final         artifacts/tables/*.parquet + *.csv — delivered to reports / end-users.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .zones import DEFAULT_PAIRS, DEFAULT_ZONES


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
    check_hourly_continuity: bool = False   # checks continuity at `freq`; name kept for compat
    freq: str = "h"                         # pandas freq of the index ("h", "15min", ...)


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
    check_hourly_continuity: bool = False   # checks continuity at `freq`; name kept for compat
    freq: str = "h"                         # pandas freq of the index ("h", "15min", ...)
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
def build_prices_schema(zones: list[str] | None = DEFAULT_ZONES, freq: str = "h") -> DataFrameSchema:
    return DataFrameSchema(
        name="prices",
        index_name="time",
        index_dtype="datetime64[ns, UTC]",
        columns=[
            ColumnSchema(z, dtype="float64", nullable=True, min_value=-500.0, max_value=3000.0, unit="EUR/MWh")
            for z in (zones or DEFAULT_ZONES)
        ],
        allow_extra_columns=True,
        min_rows=168,
        check_hourly_continuity=True,
        freq=freq,
    )


#: Actual total load, hourly UTC.
#: Observed 2025 maxima: NO1 7169, NO2 6052, NO3 4667, NO4 3370, NO5 3147 MW.
def build_load_schema(zones: list[str] | None = DEFAULT_ZONES, freq: str = "h") -> DataFrameSchema:
    return DataFrameSchema(
        name="load",
        index_name="time",
        index_dtype="datetime64[ns, UTC]",
        columns=[
            ColumnSchema(z, dtype="float64", nullable=True, min_value=0.0, max_value=15000.0, unit="MW")
            for z in (zones or DEFAULT_ZONES)
        ],
        allow_extra_columns=True,
        min_rows=168,
        check_hourly_continuity=True,
        freq=freq,
    )


#: Actual generation per production type, hourly UTC.
#: MultiIndex columns: level 0 = zone, level 1 = ENTSO-E type name.
#: Level names = ["_zone", "_type"] as set by pivot_table in load_actgen_table.
#: Not every (zone, type) combination exists — inactive pairs are simply absent.
#: Generation is always non-negative; upper bound 20 000 MW covers total NO hydro capacity.
def build_gen_schema(zones: list[str] | None = DEFAULT_ZONES, freq: str = "h") -> MultiIndexDataFrameSchema:
    return MultiIndexDataFrameSchema(
        name="actgen",
        index_name=None,            # pivot_table sets "_time"; reindex(idx) clears it
        index_dtype="datetime64[ns, UTC]",
        min_rows=168,
        check_hourly_continuity=True,
        freq=freq,
        expected_level0=list(zones or DEFAULT_ZONES),
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
            "Nuclear",            # SE3, FI
            "Biomass",            # SE, FI
            "Fossil Peat",        # FI
        ],
        level_names=["_zone", "_type"],
        min_value=0.0,
        max_value=20000.0,
        unit="MW",
    )


#: Internal net physical flows between zone pairs, hourly UTC.
#: Columns: "ZoneA-ZoneB" where ZoneA < ZoneB (lexicographic).
#: Values: net MW toward the higher-sorted zone; negative = reverse direction.
def build_transmission_schema(pairs: list[str] | None = DEFAULT_PAIRS, freq: str = "h") -> DataFrameSchema:
    return DataFrameSchema(
        name="transmission",
        index_name="time",
        index_dtype="datetime64[ns, UTC]",
        columns=[
            ColumnSchema(p, dtype="float64", nullable=True, min_value=-5000.0, max_value=5000.0, unit="MW")
            for p in (pairs or DEFAULT_PAIRS)
        ],
        allow_extra_columns=True,
        min_rows=168,
        check_hourly_continuity=True,
        freq=freq,
    )


#: Net external import balance per zone, hourly UTC.
#: Values: net MW import from outside the configured zone set; positive = importing, negative = exporting.
def build_external_balance_schema(zones: list[str] | None = DEFAULT_ZONES, freq: str = "h") -> DataFrameSchema:
    return DataFrameSchema(
        name="external_balance",
        index_name="time",
        index_dtype="datetime64[ns, UTC]",
        columns=[
            ColumnSchema(z, dtype="float64", nullable=True, min_value=-10000.0, max_value=10000.0, unit="MW")
            for z in (zones or DEFAULT_ZONES)
        ],
        allow_extra_columns=False,
        min_rows=168,
        check_hourly_continuity=True,
        freq=freq,
    )


# ---------------------------------------------------------------------------
# Reserve markets (#16), probabilistic forecast (#18) — zone/market builders
# ---------------------------------------------------------------------------

RESERVE_MARKETS = ["mfrr_eam", "afrr", "fcr_d"]
RESERVE_FIELDS = ["up_price", "down_price", "up_volume", "down_volume"]


def build_reserves_schema(market: str, zones: list[str] | None = None, freq: str = "h") -> MultiIndexDataFrameSchema:
    """Reserve-market series (mFRR EAM, aFRR, FCR-D).

    MultiIndex columns: level 0 = zone, level 1 = field (up/down price [EUR/MW(h)]
    and volume [MW]). Reserve series legitimately have gaps (MTUs with no
    activation), so continuity is not enforced.
    """
    return MultiIndexDataFrameSchema(
        name=f"reserves_{market}",
        index_name=None,
        index_dtype="datetime64[ns, UTC]",
        min_rows=24,
        check_hourly_continuity=False,
        freq=freq,
        expected_level0=[],              # any subset of configured zones may be present
        expected_level1=list(RESERVE_FIELDS),
        level_names=["_zone", "_field"],
        min_value=-10000.0,              # down-regulation prices can be deeply negative
        max_value=15000.0,               # balancing prices spike far above day-ahead
        unit="EUR/MWh | MW",
    )


def build_forecast_schema(zones: list[str] | None = None, freq: str = "h") -> MultiIndexDataFrameSchema:
    """Per-zone probabilistic day-ahead bands: level 0 = zone, level 1 = p10/p50/p90."""
    return MultiIndexDataFrameSchema(
        name="price_forecast",
        index_name=None,
        index_dtype="datetime64[ns, UTC]",
        min_rows=24,
        check_hourly_continuity=False,
        freq=freq,
        expected_level0=[],
        expected_level1=["p10", "p50", "p90"],
        level_names=["_zone", "_band"],
        min_value=-2000.0,
        max_value=5000.0,
        unit="EUR/MWh",
    )


def build_scenarios_schema(zones: list[str] | None = None, freq: str = "h") -> MultiIndexDataFrameSchema:
    """Per-zone scenario ensemble: level 0 = zone, level 1 = scenario id ("s000", ...)."""
    return MultiIndexDataFrameSchema(
        name="price_scenarios",
        index_name=None,
        index_dtype="datetime64[ns, UTC]",
        min_rows=24,
        check_hourly_continuity=False,
        freq=freq,
        expected_level0=[],
        expected_level1=[],              # any number of scenario columns
        level_names=["_zone", "_scenario"],
        min_value=-2000.0,
        max_value=5000.0,
        unit="EUR/MWh",
    )


PRICES_SCHEMA = build_prices_schema(DEFAULT_ZONES)
LOAD_SCHEMA = build_load_schema(DEFAULT_ZONES)
GEN_SCHEMA = build_gen_schema(DEFAULT_ZONES)
TRANSMISSION_INTERNAL_SCHEMA = build_transmission_schema(DEFAULT_PAIRS)
EXTERNAL_BALANCE_SCHEMA = build_external_balance_schema(DEFAULT_ZONES)


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

FORECAST_BACKTEST_SCHEMA = DataFrameSchema(
    name="forecast_backtest",
    columns=[
        ColumnSchema("zone",              dtype="object",  nullable=False),
        ColumnSchema("rmse",              dtype="float64", nullable=True, min_value=0.0, unit="EUR/MWh"),
        ColumnSchema("pinball_q10",       dtype="float64", nullable=True, min_value=0.0, unit="EUR/MWh"),
        ColumnSchema("pinball_q50",       dtype="float64", nullable=True, min_value=0.0, unit="EUR/MWh"),
        ColumnSchema("pinball_q90",       dtype="float64", nullable=True, min_value=0.0, unit="EUR/MWh"),
        ColumnSchema("coverage_p10_p90",  dtype="float64", nullable=True, min_value=0.0, max_value=1.0),
        ColumnSchema("n_periods",         dtype="float64", nullable=True, min_value=1.0),
    ],
    allow_extra_columns=True,
    min_rows=1,
)

REVENUE_P50P90_SCHEMA = DataFrameSchema(
    name="revenue_p50_p90",
    columns=[
        ColumnSchema("zone",                dtype="object",  nullable=False),
        ColumnSchema("p50_eur_yr",          dtype="float64", nullable=True, unit="EUR/yr"),
        ColumnSchema("p90_eur_yr",          dtype="float64", nullable=True, unit="EUR/yr"),
        ColumnSchema("p10_eur_yr",          dtype="float64", nullable=True, unit="EUR/yr"),
        ColumnSchema("mean_eur_yr",         dtype="float64", nullable=True, unit="EUR/yr"),
        ColumnSchema("p50_eur_per_kw_yr",   dtype="float64", nullable=True, unit="EUR/kW/yr"),
        ColumnSchema("p90_eur_per_kw_yr",   dtype="float64", nullable=True, unit="EUR/kW/yr"),
        ColumnSchema("realism_gap_pct",     dtype="float64", nullable=True, unit="%"),
        ColumnSchema("n_scenarios",         dtype="float64", nullable=True, min_value=1.0),
        ColumnSchema("eval_hours",          dtype="float64", nullable=True, min_value=1.0),
    ],
    allow_extra_columns=True,
    min_rows=1,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: dict[str, DataFrameSchema | MultiIndexDataFrameSchema] = {
    # intermediate
    "prices":            PRICES_SCHEMA,
    "load":              LOAD_SCHEMA,
    "actgen":            GEN_SCHEMA,
    "transmission":      TRANSMISSION_INTERNAL_SCHEMA,
    "external_balance":  EXTERNAL_BALANCE_SCHEMA,
    "reserves_mfrr_eam": build_reserves_schema("mfrr_eam"),
    "reserves_afrr":     build_reserves_schema("afrr"),
    "reserves_fcr_d":    build_reserves_schema("fcr_d"),
    "price_forecast":    build_forecast_schema(),
    "price_scenarios":   build_scenarios_schema(),
    # final
    "valuation_pf":      VALUATION_PF_SCHEMA,
    "valuation_rh":      VALUATION_RH_SCHEMA,
    "forecast_backtest": FORECAST_BACKTEST_SCHEMA,
    "revenue_p50_p90":   REVENUE_P50P90_SCHEMA,
}

#: Schema builders keyed by artifact name, for callers that need a schema
#: parameterized by the actual configured zones/pairs and/or target resolution
#: (freq) rather than the NO1-NO5 hourly default baked into SCHEMA_REGISTRY.
#: Each builder accepts ``(zones_or_pairs=None, freq="h")``.
SCHEMA_BUILDERS = {
    "prices":            build_prices_schema,
    "load":              build_load_schema,
    "actgen":            build_gen_schema,
    "transmission":      build_transmission_schema,
    "external_balance":  build_external_balance_schema,
    "reserves_mfrr_eam": lambda zones=None, freq="h": build_reserves_schema("mfrr_eam", zones, freq),
    "reserves_afrr":     lambda zones=None, freq="h": build_reserves_schema("afrr", zones, freq),
    "reserves_fcr_d":    lambda zones=None, freq="h": build_reserves_schema("fcr_d", zones, freq),
    "price_forecast":    build_forecast_schema,
    "price_scenarios":   build_scenarios_schema,
}
