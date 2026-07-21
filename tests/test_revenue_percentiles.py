"""Tests for issue #21 — P50/P90 revenue methodology."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from revenue_percentiles import (
    annual_revenue_distribution,
    percentile_report,
    realism_gap_from_valuation_rh,
    scenario_revenue,
)

P_MW, E_MWH, ETA = 10.0, 40.0, 0.9


def _price(days: int = 45, seed: int = 9) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=days * 24, freq="h", tz="UTC")
    hod = idx.hour
    shape = 25 * np.sin(2 * np.pi * (hod - 8) / 24.0)
    return pd.Series(60 + shape + rng.normal(0, 5, len(idx)), index=idx)


class TestScenarioRevenue:
    def test_positive_for_spready_day(self):
        idx = pd.date_range("2025-01-01", periods=24, freq="h", tz="UTC")
        path = pd.Series(50 + 30 * np.sin(2 * np.pi * (idx.hour - 8) / 24.0), index=idx)
        assert scenario_revenue(path, P_MW, E_MWH, ETA) > 0

    def test_zero_for_flat_prices(self):
        idx = pd.date_range("2025-01-01", periods=24, freq="h", tz="UTC")
        path = pd.Series(50.0, index=idx)
        assert scenario_revenue(path, P_MW, E_MWH, ETA) == pytest.approx(0.0, abs=1.0)


class TestDistribution:
    def test_distribution_and_percentiles(self):
        revenues, meta = annual_revenue_distribution(
            _price(), P_MW, E_MWH, ETA,
            n_scenarios=12, eval_days=10, realism_gap_pct=15.0, seed=42,
            forecaster_kwargs={"n_estimators": 25},
        )
        assert revenues.shape == (12,)
        assert (revenues > 0).all()
        report = percentile_report(revenues, P_MW, meta, "NO1")
        # Lender convention: P90 (exceeded 90% of years) <= P50 <= P10.
        assert report["p90_eur_yr"] <= report["p50_eur_yr"] <= report["p10_eur_yr"]
        assert report["p50_eur_per_kw_yr"] == pytest.approx(report["p50_eur_yr"] / (P_MW * 1000))
        assert report["realism_gap_pct"] == 15.0

    def test_realism_gap_scales_revenue(self):
        kwargs = dict(n_scenarios=6, eval_days=10, seed=42, forecaster_kwargs={"n_estimators": 25})
        rev_no_gap, _ = annual_revenue_distribution(_price(), P_MW, E_MWH, ETA, realism_gap_pct=0.0, **kwargs)
        rev_gap, _ = annual_revenue_distribution(_price(), P_MW, E_MWH, ETA, realism_gap_pct=20.0, **kwargs)
        np.testing.assert_allclose(rev_gap, rev_no_gap * 0.8, rtol=1e-9)

    def test_reproducible_with_seed(self):
        kwargs = dict(n_scenarios=5, eval_days=10, realism_gap_pct=10.0, seed=7,
                      forecaster_kwargs={"n_estimators": 25})
        a, _ = annual_revenue_distribution(_price(), P_MW, E_MWH, ETA, **kwargs)
        b, _ = annual_revenue_distribution(_price(), P_MW, E_MWH, ETA, **kwargs)
        np.testing.assert_array_equal(a, b)


class TestRealismGapSource:
    def test_uses_measured_gap_when_stochastic_run_exists(self, tmp_path):
        tables = tmp_path / "tables"
        tables.mkdir()
        pd.DataFrame([
            {"zone": "NO1", "mode": "stochastic", "gap_vs_perfect_rh_pct": 22.5},
            {"zone": "NO2", "mode": "perfect", "gap_vs_perfect_rh_pct": 0.0},
        ]).to_parquet(tables / "valuation_rh.parquet")
        paths = {"tables": tables}
        assert realism_gap_from_valuation_rh(paths, "NO1", default_pct=15.0) == 22.5
        # perfect-mode rows carry no realistic gap information -> default.
        assert realism_gap_from_valuation_rh(paths, "NO2", default_pct=15.0) == 15.0
        assert realism_gap_from_valuation_rh(paths, "NO5", default_pct=15.0) == 15.0

    def test_default_when_no_file(self, tmp_path):
        paths = {"tables": tmp_path}
        assert realism_gap_from_valuation_rh(paths, "NO1", default_pct=12.0) == 12.0
