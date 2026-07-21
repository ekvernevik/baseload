"""Tests for issue #19 — stochastic MPC rolling horizon with CVaR."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bess_valuation_rh import (
    run_rh,
    run_rh_stochastic,
    solve_window,
    solve_window_stochastic,
)

P_MW, E_MWH, ETA = 10.0, 40.0, 0.9


def _price_series(days: int, seed: int = 5) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=days * 24, freq="h", tz="UTC")
    hod = idx.hour
    shape = 25 * np.sin(2 * np.pi * (hod - 8) / 24.0)
    noise = np.zeros(len(idx))
    for i in range(1, len(idx)):
        noise[i] = 0.9 * noise[i - 1] + rng.normal(0, 3)
    return pd.Series(55 + shape + noise, index=idx)


def _scenario_frame(n_scen: int = 8, spike_scen: int = 0) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=12, freq="h", tz="UTC")
    base = 50 + 20 * np.sin(np.linspace(0, 2 * np.pi, 12))
    rng = np.random.default_rng(0)
    data = {f"s{s:03d}": base + rng.normal(0, 5, 12) for s in range(n_scen)}
    df = pd.DataFrame(data, index=idx)
    if spike_scen is not None:
        df.iloc[0, spike_scen] = -200.0  # one scenario with a first-hour crash
    return df


class TestStochasticWindow:
    def test_returns_feasible_plan(self):
        scen = _scenario_frame()
        ch_plan, dis_plan, soc_plan = solve_window_stochastic(scen, P_MW, E_MWH, ETA, soc0=0.5 * E_MWH)
        assert len(ch_plan) == len(dis_plan) == len(soc_plan) == len(scen)
        assert all(0 - 1e-6 <= c <= P_MW + 1e-6 for c in ch_plan)
        assert all(0 - 1e-6 <= d <= P_MW + 1e-6 for d in dis_plan)
        assert all(0.1 * E_MWH - 1e-5 <= s <= 0.9 * E_MWH + 1e-5 for s in soc_plan)

    def test_reduces_to_deterministic_when_one_scenario(self):
        idx = pd.date_range("2025-01-01", periods=12, freq="h", tz="UTC")
        path = pd.Series(50 + 30 * np.sin(np.linspace(0, 2 * np.pi, 12)), index=idx)
        ch_det, dis_det, _ = solve_window(path, P_MW, E_MWH, ETA, soc0=0.5 * E_MWH)
        ch_st, dis_st, _ = solve_window_stochastic(
            path.to_frame("s000"), P_MW, E_MWH, ETA, soc0=0.5 * E_MWH, cvar_weight=0.0
        )
        assert ch_st[0] == pytest.approx(ch_det[0], abs=1e-4)
        assert dis_st[0] == pytest.approx(dis_det[0], abs=1e-4)

    def test_cvar_makes_dispatch_more_conservative(self):
        # With a crash scenario present, a heavily risk-weighted objective must
        # not discharge more in period 0 than the risk-neutral one.
        scen = _scenario_frame(spike_scen=0)
        scen.iloc[1:, 0] = -200.0  # scenario 0: prices crash for the whole window
        _, dis_neutral, _ = solve_window_stochastic(
            scen, P_MW, E_MWH, ETA, soc0=0.5 * E_MWH, cvar_weight=0.0
        )
        _, dis_averse, _ = solve_window_stochastic(
            scen, P_MW, E_MWH, ETA, soc0=0.5 * E_MWH, cvar_alpha=0.85, cvar_weight=5.0
        )
        assert dis_averse[0] <= dis_neutral[0] + 1e-6


class TestStochasticRolling:
    def test_causal_and_profitable(self):
        price = _price_series(days=24)
        rev, soc, eval_index = run_rh_stochastic(
            price, P_MW, E_MWH, ETA, lookahead=12, step=4,
            n_scenarios=5, warmup_days=15,
            forecaster_kwargs={"n_estimators": 25},
        )
        # Dispatch only over the post-warmup window.
        assert eval_index[0] == price.index[15 * 24]
        assert len(soc) == len(eval_index)
        # A predictable daily shape should be monetisable without foresight.
        assert rev > 0

    def test_stochastic_below_perfect_foresight(self):
        price = _price_series(days=24)
        rev_stoch, _, eval_index = run_rh_stochastic(
            price, P_MW, E_MWH, ETA, lookahead=12, step=4,
            n_scenarios=5, warmup_days=15,
            forecaster_kwargs={"n_estimators": 25},
        )
        rev_perfect, _ = run_rh(price.reindex(eval_index), P_MW, E_MWH, ETA, lookahead=12, step=4)
        # Without foresight you can't beat foresight (small numerical slack).
        assert rev_stoch <= rev_perfect * 1.02 + 1e-6
        # But the causal policy should still capture a meaningful share.
        assert rev_stoch > 0.3 * rev_perfect

    def test_warmup_guard(self):
        price = _price_series(days=10)
        with pytest.raises(ValueError, match="warmup"):
            run_rh_stochastic(price, P_MW, E_MWH, ETA, lookahead=12, step=4, warmup_days=28)
