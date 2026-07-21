"""Tests for issue #18 — probabilistic per-zone day-ahead forecast."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from price_forecast import (
    QuantileForecaster,
    backtest_zone,
    build_features,
    periods_per_day,
    pinball_loss,
)


def _synthetic_price(days: int = 120, freq: str = "h", seed: int = 3) -> pd.Series:
    """Price with a daily shape + weekly effect + AR noise — forecastable."""
    rng = np.random.default_rng(seed)
    ppd = int(round(24 / (0.25 if freq == "15min" else 1.0)))
    idx = pd.date_range("2025-01-01", periods=days * ppd, freq=freq, tz="UTC")
    hod = idx.hour + idx.minute / 60.0
    shape = 20 * np.sin(2 * np.pi * (hod - 8) / 24.0)
    weekend = np.where(idx.dayofweek >= 5, -10.0, 0.0)
    noise = np.zeros(len(idx))
    for i in range(1, len(idx)):
        noise[i] = 0.95 * noise[i - 1] + rng.normal(0, 2)
    return pd.Series(60 + shape + weekend + noise, index=idx, name="NO1")


class TestFeatures:
    def test_features_are_causal(self):
        price = _synthetic_price(days=30)
        feats = build_features(price)
        # Perturb the future: features up to t must not change.
        bumped = price.copy()
        bumped.iloc[-24:] += 1000.0
        feats2 = build_features(bumped)
        cutoff = price.index[-25]
        pd.testing.assert_frame_equal(feats.loc[:cutoff], feats2.loc[:cutoff])

    def test_periods_per_day(self):
        assert periods_per_day(_synthetic_price(days=10, freq="h")) == 24
        assert periods_per_day(_synthetic_price(days=10, freq="15min")) == 96


class TestQuantileForecaster:
    def test_bands_do_not_cross_and_order(self):
        price = _synthetic_price()
        fc = QuantileForecaster(n_estimators=40).fit(price[:-24])
        target = price.index[-24:]
        bands = fc.predict(price[:-24], target)
        assert list(bands.columns) == ["p10", "p50", "p90"]
        assert (bands["p10"] <= bands["p50"]).all()
        assert (bands["p50"] <= bands["p90"]).all()

    def test_scenarios_shape_and_anchor(self):
        price = _synthetic_price()
        fc = QuantileForecaster(n_estimators=40).fit(price[:-24])
        target = price.index[-24:]
        scen = fc.scenarios(price[:-24], target, n_scenarios=30, seed=0)
        assert scen.shape == (24, 30)
        bands = fc.predict(price[:-24], target)
        # Ensemble mean tracks the P50 anchor.
        assert np.abs(scen.mean(axis=1) - bands["p50"]).mean() < 10.0

    def test_scenarios_deterministic_with_seed(self):
        price = _synthetic_price()
        fc = QuantileForecaster(n_estimators=40).fit(price[:-24])
        target = price.index[-24:]
        a = fc.scenarios(price[:-24], target, n_scenarios=5, seed=1)
        b = fc.scenarios(price[:-24], target, n_scenarios=5, seed=1)
        pd.testing.assert_frame_equal(a, b)

    def test_requires_p50(self):
        with pytest.raises(ValueError, match="0.5"):
            QuantileForecaster(quantiles=(0.1, 0.9))

    def test_works_at_15min(self):
        price = _synthetic_price(days=40, freq="15min")
        fc = QuantileForecaster(n_estimators=30).fit(price[:-96])
        bands = fc.predict(price[:-96], price.index[-96:])
        assert len(bands) == 96


class TestBacktest:
    def test_out_of_sample_metrics(self):
        price = _synthetic_price(days=80)
        bands, scens, metrics = backtest_zone(
            price, backtest_days=14, n_scenarios=10, n_estimators=40, seed=42
        )
        assert metrics["n_periods"] == 14 * 24
        # AR(0.95) noise with sigma 2 is highly predictable from lags + shape;
        # a sane model lands well under the ~15 EUR/MWh naive-mean error.
        assert metrics["rmse"] < 12.0
        # P10-P90 band should cover most realised prices.
        assert metrics["coverage_p10_p90"] > 0.5
        assert metrics["pinball_q50"] <= metrics["rmse"]

    def test_pinball_loss_basic(self):
        y = np.array([10.0])
        assert pinball_loss(y, np.array([8.0]), 0.5) == pytest.approx(1.0)
        assert pinball_loss(y, np.array([12.0]), 0.9) == pytest.approx(0.2)

    def test_insufficient_history_raises(self):
        price = _synthetic_price(days=20)
        with pytest.raises(ValueError, match="Not enough history"):
            backtest_zone(price, backtest_days=15)
