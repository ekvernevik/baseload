"""Proves issue #16: reserve-market series land in processed data with
schema validation, for at least one zone/product.

Builds synthetic ENTSO-E-shaped long-format CSVs for mFRR EAM capacity
(zone NO1) and PICASSO aFRR activation (zone SE3, proving the loader
generalizes beyond a single hardcoded zone/product), runs them through
``load_reserve_table``, and validates the result against the matching
``build_reserve_schema`` output.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from baseload.schemas import build_reserve_schema
from baseload.validators import validate_dataframe
from ingest_reserves import load_reserve_table

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


def _reserve_csv(idx: pd.DatetimeIndex, up_price: float, up_volume: float, down_price: float, down_volume: float) -> pd.DataFrame:
    mtu = _mtu_range(idx)
    return pd.DataFrame({
        "MTU": mtu * 2,
        "Direction": ["Up"] * len(idx) + ["Down"] * len(idx),
        "Price [EUR/MWh]": [up_price] * len(idx) + [down_price] * len(idx),
        "Volume [MW]": [up_volume] * len(idx) + [down_volume] * len(idx),
    })


def test_mfrr_capacity_single_zone(tmp_path: Path, idx: pd.DatetimeIndex):
    df = _reserve_csv(idx, up_price=25.0, up_volume=80.0, down_price=5.0, down_volume=60.0)
    (tmp_path / "no1_mfrr_capacity.csv").write_text(df.to_csv(index=False))

    table = load_reserve_table({"NO1": "no1_mfrr_capacity.csv"}, tmp_path, START, str(END))

    assert set(table.columns) == {
        "NO1_up_price", "NO1_up_volume", "NO1_down_price", "NO1_down_volume",
    }
    assert (table["NO1_up_price"] == 25.0).all()
    assert (table["NO1_down_volume"] == 60.0).all()
    assert not table.isna().any().any()

    schema = build_reserve_schema("mfrr_capacity", ["NO1"])
    assert validate_dataframe(table, schema, raise_on_error=False) == []


def test_afrr_activation_generalizes_to_other_zone(tmp_path: Path, idx: pd.DatetimeIndex):
    """Same loader, a different product name and a non-NO zone — no code change needed."""
    df = _reserve_csv(idx, up_price=120.0, up_volume=40.0, down_price=-10.0, down_volume=15.0)
    (tmp_path / "se3_afrr_activation.csv").write_text(df.to_csv(index=False))

    table = load_reserve_table({"SE3": "se3_afrr_activation.csv"}, tmp_path, START, str(END))

    assert set(table.columns) == {
        "SE3_up_price", "SE3_up_volume", "SE3_down_price", "SE3_down_volume",
    }
    assert (table["SE3_down_price"] == -10.0).all()

    schema = build_reserve_schema("afrr_activation", ["SE3"])
    assert validate_dataframe(table, schema, raise_on_error=False) == []
