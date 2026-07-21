"""Tests for issue #14 (15-min MTU ingest) layered on the #15 zone API.

Uses synthetic ENTSO-E-format CSV exports (range MTU timestamps) so the whole
ingest -> schema-validated parquet path runs without network access or raw data.
Schemas are built per-config via baseload.schemas.build_*_schema and the io
layer's zones=/freq= parameters (baseload.zones convention).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from baseload.io import read_parquet, write_parquet
from baseload.schemas import build_prices_schema, build_transmission_schema
from baseload.pipeline_utils import (
    freq_hours,
    index_dt_hours,
    resolution_freq,
    standardize_series,
)
from baseload.validators import SchemaError, validate_dataframe
from ingest_entsoe import _make_zone_extractor, load_zone_table


def _write_entsoe_price_csv(path, start: str, periods: int, freq: str, values=None):
    """Write a synthetic ENTSO-E day-ahead price export with range MTUs."""
    idx = pd.date_range(start, periods=periods, freq=freq)
    step = idx[1] - idx[0]
    if values is None:
        rng = np.random.default_rng(0)
        values = 50 + 20 * np.sin(np.arange(periods) / 12) + rng.normal(0, 2, periods)
    fmt = "%d/%m/%Y %H:%M:%S"
    mtu = [f"{ts.strftime(fmt)} - {(ts + step).strftime(fmt)}" for ts in idx]
    pd.DataFrame({"MTU": mtu, "Day-ahead Price [EUR/MWh]": values}).to_csv(path, index=False)
    return idx, np.asarray(values, dtype=float)


# ---------------------------------------------------------------------------
# Resolution config plumbing
# ---------------------------------------------------------------------------

class TestResolutionConfig:
    def test_default_is_15min(self):
        assert resolution_freq({}) == "15min"

    def test_hourly_selectable(self):
        assert resolution_freq({"resolution": "hourly"}) == "h"
        assert resolution_freq({"resolution": "60min"}) == "h"

    def test_unknown_resolution_raises(self):
        with pytest.raises(ValueError, match="Unknown resolution"):
            resolution_freq({"resolution": "5min"})

    def test_freq_hours(self):
        assert freq_hours("15min") == 0.25
        assert freq_hours("h") == 1.0


# ---------------------------------------------------------------------------
# standardize_series native/target resolution handling
# ---------------------------------------------------------------------------

class TestStandardizeResolution:
    def test_15min_native_preserved(self, tmp_path):
        csv = tmp_path / "no1.csv"
        _, values = _write_entsoe_price_csv(csv, "2025-10-01", 96 * 3, "15min")
        out = standardize_series(pd.read_csv(csv), zone="NO1", value_name="price", freq="15min")
        assert index_dt_hours(out.index) == 0.25
        np.testing.assert_allclose(out.values[:96], values[:96])

    def test_15min_source_aggregates_to_hourly(self, tmp_path):
        csv = tmp_path / "no1.csv"
        values = np.tile([10.0, 20.0, 30.0, 40.0], 24 * 3)
        _write_entsoe_price_csv(csv, "2025-10-01", 96 * 3, "15min", values=values)
        out = standardize_series(pd.read_csv(csv), zone="NO1", value_name="price", freq="h")
        assert index_dt_hours(out.index) == 1.0
        np.testing.assert_allclose(out.values, 25.0)  # mean of the four quarters

    def test_hourly_source_forward_fills_to_15min(self, tmp_path):
        csv = tmp_path / "no1.csv"
        values = np.arange(72, dtype=float)
        _write_entsoe_price_csv(csv, "2025-01-01", 72, "h", values=values)
        out = standardize_series(pd.read_csv(csv), zone="NO1", value_name="price", freq="15min")
        assert index_dt_hours(out.index) == 0.25
        np.testing.assert_allclose(out.iloc[:4].values, 0.0)  # step function over the MTU
        assert not out.iloc[:-3].isna().any()

    def test_hourly_source_does_not_fill_across_gaps(self, tmp_path):
        csv = tmp_path / "no1.csv"
        idx = pd.date_range("2025-01-01", periods=72, freq="h")
        keep = [ts for ts in idx if ts.day != 2]  # drop a whole day
        fmt = "%d/%m/%Y %H:%M:%S"
        mtu = [f"{ts.strftime(fmt)} - {(ts + pd.Timedelta(hours=1)).strftime(fmt)}" for ts in keep]
        pd.DataFrame({"MTU": mtu, "Price": 42.0}).to_csv(csv, index=False)
        out = standardize_series(pd.read_csv(csv), zone="NO1", value_name="price", freq="15min")
        gap = out.loc["2025-01-02 06:00":"2025-01-02 18:00"]
        assert gap.isna().all()


# ---------------------------------------------------------------------------
# End-to-end: ingest -> schema-validated parquet at 15-min (issue #14 done-when)
# ---------------------------------------------------------------------------

class TestEndToEnd15Min:
    def test_one_zone_15min_end_to_end(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        _write_entsoe_price_csv(raw / "no1.csv", "2025-10-01", 96 * 3, "15min")

        prices = load_zone_table(
            {"NO1": "no1.csv"}, raw,
            start="2025-10-01T00:00:00Z", end="2025-10-03T23:45:00Z",
            label="price", freq="15min",
        )
        assert index_dt_hours(prices.index) == 0.25
        assert list(prices.columns) == ["NO1"]

        out = tmp_path / "prices.parquet"
        write_parquet(prices, out, schema_name="prices", zones=["NO1"], freq="15min")
        back = read_parquet(out, schema_name="prices", zones=["NO1"], freq="15min")
        pd.testing.assert_frame_equal(back, prices, check_freq=False)

    def test_hourly_still_supported(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        _write_entsoe_price_csv(raw / "no1.csv", "2025-01-01", 24 * 8, "h")
        prices = load_zone_table(
            {"NO1": "no1.csv"}, raw,
            start="2025-01-01T00:00:00Z", end="2025-01-08T23:00:00Z",
            label="price", freq="h",
        )
        assert index_dt_hours(prices.index) == 1.0
        write_parquet(prices, tmp_path / "prices.parquet", schema_name="prices", zones=["NO1"], freq="h")

    def test_validator_catches_15min_gap(self):
        idx = pd.date_range("2025-10-01", periods=300, freq="15min", tz="UTC").delete(150)
        df = pd.DataFrame({"NO1": 50.0}, index=idx)
        df.index.name = "time"
        with pytest.raises(SchemaError, match="missing '15min'"):
            validate_dataframe(df, build_prices_schema(["NO1"], freq="15min"))

    def test_hourly_validator_ignores_15min_gap(self):
        # The same gap is invisible at hourly granularity — freq must matter.
        idx = pd.date_range("2025-10-01", periods=300, freq="15min", tz="UTC").delete(150)
        df = pd.DataFrame({"NO1": 50.0}, index=idx)
        df.index.name = "time"
        assert validate_dataframe(df, build_prices_schema(["NO1"], freq="h"), raise_on_error=False) == []


# ---------------------------------------------------------------------------
# Config-driven zones (issue #15) still works with the #14 resolution layer
# ---------------------------------------------------------------------------

class TestZoneParameterisation:
    def test_se_zones_ingest_end_to_end(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        _write_entsoe_price_csv(raw / "se3.csv", "2025-10-01", 96 * 3, "15min")
        _write_entsoe_price_csv(raw / "se4.csv", "2025-10-01", 96 * 3, "15min")
        prices = load_zone_table(
            {"SE3": "se3.csv", "SE4": "se4.csv"}, raw,
            start="2025-10-01T00:00:00Z", end="2025-10-03T23:45:00Z",
            label="price", freq="15min",
        )
        assert list(prices.columns) == ["SE3", "SE4"]
        write_parquet(prices, tmp_path / "prices.parquet", schema_name="prices",
                      zones=["SE3", "SE4"], freq="15min")

    def test_schema_requires_configured_zones(self):
        idx = pd.date_range("2025-01-01", periods=200, freq="h", tz="UTC")
        df = pd.DataFrame({"NO1": 50.0}, index=idx)  # SE3 missing
        df.index.name = "time"
        with pytest.raises(SchemaError, match="SE3"):
            validate_dataframe(df, build_prices_schema(["NO1", "SE3"], freq="h"))

    def test_zone_extractor_generic(self):
        extract = _make_zone_extractor({"NO1", "NO2", "SE3", "SE4", "FI"})
        assert extract("BZN|SE3") == "SE3"
        assert extract("BZN|NO1") == "NO1"
        assert extract("BZN|FI") == "FI"
        assert extract("BZN|DK1") is None

    def test_transmission_schema_from_pairs(self):
        schema = build_transmission_schema(["NO1-SE3"], freq="h")
        assert [c.name for c in schema.columns] == ["NO1-SE3"]
