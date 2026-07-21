"""Tests for issue #17 — regime detection with physical drivers + forecast.

Synthetic ground truth: a year alternating between a "wet/windy oversupply"
regime (low price, high wind) and a "scarcity" regime (high price, low wind)
in multi-week blocks. A regime model worth the name must (a) recover the
blocks, (b) show persistence far above a shuffled baseline, and (c) emit a
forward-looking signal that predicts tomorrow's regime well above chance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score

from regime_clustering import (
    build_daily_features,
    fit_regimes,
    forecast_next_regime,
    physical_drivers_present,
    transition_matrix,
    validate_regimes,
)


def _synthetic_market(days: int = 360, block: int = 30, seed: int = 7):
    """Hourly prices + actgen with two alternating ground-truth regimes."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=days * 24, freq="h", tz="UTC")
    day_of = np.arange(days * 24) // 24
    regime = (day_of // block) % 2  # 0 = oversupply, 1 = scarcity

    base = np.where(regime == 0, 15.0, 90.0)
    vol = np.where(regime == 0, 8.0, 20.0)
    prices = base + vol * rng.standard_normal(days * 24)
    prices[regime == 0] -= 10 * (rng.random((regime == 0).sum()) < 0.2)  # negative dips

    frame = pd.DataFrame({"NO1": prices, "NO2": prices + rng.normal(0, 3, days * 24)}, index=idx)

    wind = np.where(regime == 0, 900.0, 150.0) + rng.normal(0, 50, days * 24)
    hydro = np.full(days * 24, 2000.0) + rng.normal(0, 100, days * 24)
    actgen = pd.DataFrame(
        {
            ("NO1", "Wind Onshore"): wind.clip(0),
            ("NO1", "Hydro Water Reservoir"): hydro.clip(0),
        },
        index=idx,
    )
    actgen.columns = pd.MultiIndex.from_tuples(actgen.columns, names=["_zone", "_type"])

    truth_daily = pd.Series(((np.arange(days) // block) % 2), name="truth")
    return frame, actgen, truth_daily


class TestFeatures:
    def test_wind_share_is_a_feature(self):
        prices, actgen, _ = _synthetic_market()
        daily = build_daily_features(prices, actgen=actgen)
        assert "wind_share" in daily.columns
        assert physical_drivers_present(daily) == ["wind_share"]
        assert daily["wind_share"].between(0, 1).all()

    def test_hydro_and_temperature_optional(self):
        prices, actgen, _ = _synthetic_market(days=60)
        idx = prices.index
        hydro = pd.DataFrame({"NO1": 70.0}, index=idx)
        temp = pd.DataFrame({"NO1": -5.0}, index=idx)
        daily = build_daily_features(prices, actgen, hydro, temp)
        assert set(physical_drivers_present(daily)) == {"wind_share", "hydro_fill_pct", "temperature"}


class TestRegimeRecovery:
    def test_recovers_known_regimes(self):
        prices, actgen, truth = _synthetic_market()
        daily = build_daily_features(prices, actgen=actgen)
        daily = fit_regimes(daily, k=2, seed=42)
        ari = adjusted_rand_score(truth.values[: len(daily)], daily["cluster"].values)
        assert ari > 0.8, f"regimes should recover the ground-truth blocks (ARI={ari:.2f})"

    def test_soft_probabilities_and_confidence(self):
        prices, actgen, _ = _synthetic_market()
        daily = fit_regimes(build_daily_features(prices, actgen=actgen), k=2, seed=42)
        probs = daily[["p_regime_0", "p_regime_1"]].values
        np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-6)
        assert daily["confidence"].between(0.5, 1.0 + 1e-9).all()
        # Clean synthetic separation → mostly confident days.
        assert daily["confidence"].mean() > 0.9


class TestForwardSignal:
    def test_transition_matrix_is_persistent(self):
        prices, actgen, _ = _synthetic_market()
        daily = fit_regimes(build_daily_features(prices, actgen=actgen), k=2, seed=42)
        trans = transition_matrix(daily["cluster"], k=2)
        # Rows are probability distributions.
        np.testing.assert_allclose(trans.values.sum(axis=1), 1.0, atol=1e-9)
        # 30-day blocks → strong diagonal (persistence), which is the whole
        # point of a forward-looking signal vs. i.i.d. daily labels.
        assert (np.diag(trans.values) > 0.85).all()

    def test_forecast_predicts_next_day(self):
        prices, actgen, _ = _synthetic_market()
        daily = fit_regimes(build_daily_features(prices, actgen=actgen), k=2, seed=42)
        trans = transition_matrix(daily["cluster"], k=2)
        forecast = forecast_next_regime(daily, trans, k=2)
        assert list(forecast.columns[:2]) == ["p_next_regime_0", "p_next_regime_1"]
        predicted = forecast["expected_next_regime"].values[:-1]
        actual = daily["cluster"].values[1:]
        accuracy = (predicted == actual).mean()
        assert accuracy > 0.9, f"next-day regime accuracy {accuracy:.2f}"


class TestValidation:
    def test_persistence_beats_shuffled_baseline(self):
        prices, actgen, _ = _synthetic_market()
        daily = fit_regimes(build_daily_features(prices, actgen=actgen), k=2, seed=42)
        metrics = validate_regimes(daily, k=2).set_index("metric")["value"]
        assert metrics["mean_run_length_days"] > 3 * metrics["mean_run_length_shuffled"]
        assert metrics["silhouette"] > 0.2
        assert 0.0 <= metrics["low_confidence_share"] <= 0.2
