"""Proves issue #15: a non-NO zone set runs through ingest with config only.

Builds synthetic ENTSO-E-shaped CSVs for zones SE3/SE4 (never configured
anywhere in this repo's demo pipeline) and drives them through
``load_zone_table`` / ``load_transmission_table`` in ``ingest_entsoe.py``,
then validates the results against schemas built for that exact zone/pair
set via ``baseload.schemas``. No code change is required to support SE3/SE4
here — only the zone/pair arguments differ from the NO1-NO5 default.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from baseload.schemas import build_external_balance_schema, build_prices_schema, build_transmission_schema
from baseload.validators import validate_dataframe
from ingest_entsoe import load_transmission_table, load_zone_table

N_HOURS = 200
START = "2025-01-01T00:00:00Z"
END = pd.Timestamp(START) + pd.Timedelta(hours=N_HOURS - 1)


def _mtu_range(idx: pd.DatetimeIndex) -> list[str]:
    return [
        f"{t.strftime('%d/%m/%Y %H:%M:%S')} - {(t + pd.Timedelta(hours=1)).strftime('%d/%m/%Y %H:%M:%S')}"
        for t in idx
    ]


@pytest.fixture
def idx() -> pd.DatetimeIndex:
    return pd.date_range(START, END, freq="h", tz="UTC")


def test_load_zone_table_handles_non_no_zones(tmp_path: Path, idx: pd.DatetimeIndex):
    """Prices for SE3/SE4 ingest and validate against a schema built for those zones."""
    input_cfg = {}
    for zone, base_price in [("SE3", 40.0), ("SE4", 45.0)]:
        df = pd.DataFrame({"MTU": _mtu_range(idx), "Day-ahead Price [EUR/MWh]": base_price})
        path = tmp_path / f"{zone.lower()}_prices.csv"
        df.to_csv(path, index=False)
        input_cfg[zone] = path.name

    prices = load_zone_table(input_cfg, tmp_path, START, str(END), label="price")

    assert list(prices.columns) == ["SE3", "SE4"]
    assert not prices.isna().any().any()

    schema = build_prices_schema(["SE3", "SE4"])
    problems = validate_dataframe(prices, schema, raise_on_error=False)
    assert problems == []


def test_load_transmission_table_handles_non_no_zones(tmp_path: Path, idx: pd.DatetimeIndex):
    """SE3<->SE4 internal flow + external flows to unconfigured zones (DK1/FI)."""
    mtu = _mtu_range(idx)

    # Both zone files report the same SE3->SE4 physical flow row (ENTSO-E convention),
    # plus one external row each (to a zone that is NOT in `zones`).
    se3_df = pd.DataFrame({
        "MTU": mtu * 2,
        "Out Area": ["BZN|SE3"] * len(idx) + ["BZN|SE3"] * len(idx),
        "In Area": ["BZN|SE4"] * len(idx) + ["BZN|DK1"] * len(idx),
        "Flow [MW]": [100.0] * len(idx) + [50.0] * len(idx),
    })
    se4_df = pd.DataFrame({
        "MTU": mtu * 2,
        "Out Area": ["BZN|SE3"] * len(idx) + ["BZN|SE4"] * len(idx),
        "In Area": ["BZN|SE4"] * len(idx) + ["BZN|FI"] * len(idx),
        "Flow [MW]": [100.0] * len(idx) + [30.0] * len(idx),
    })
    (tmp_path / "se3_transmission.csv").write_text(se3_df.to_csv(index=False))
    (tmp_path / "se4_transmission.csv").write_text(se4_df.to_csv(index=False))

    input_cfg = {"SE3": "se3_transmission.csv", "SE4": "se4_transmission.csv"}
    internal_df, external_df = load_transmission_table(
        input_cfg, tmp_path, START, str(END), zones={"SE3", "SE4"}
    )

    assert list(internal_df.columns) == ["SE3-SE4"]
    assert (internal_df["SE3-SE4"] == 100.0).all()

    assert set(external_df.columns) == {"SE3", "SE4"}
    assert (external_df["SE3"] == -50.0).all()   # SE3 exporting to DK1 = leaving = negative import
    assert (external_df["SE4"] == -30.0).all()   # SE4 exporting to FI = leaving = negative import

    transmission_schema = build_transmission_schema(["SE3-SE4"])
    assert validate_dataframe(internal_df, transmission_schema, raise_on_error=False) == []

    external_schema = build_external_balance_schema(["SE3", "SE4"])
    assert validate_dataframe(external_df, external_schema, raise_on_error=False) == []
