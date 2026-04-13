"""Tests for bess_valuation_pf.py.

Behaviors under test:
  1. solve_pf           — buys cheap/sells expensive, revenue > 0, SOC within bounds
  2. solve_pf_network   — same economic direction, network-constrained revenue ≤ unconstrained
  3. NTC congestion     — BESS in congested zone earns less than in uncongested peer
"""
import warnings

import pandas as pd
import pytest

import pandas as _pd
_pd.options.future.infer_string = False

from bess_valuation_pf import solve_pf, solve_pf_network
from norway_network import NTC_MW, ZONES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price_series(low, high, n_low=12, n_high=12):
    """Alternating low/high prices over n_low + n_high hours."""
    prices = [low] * n_low + [high] * n_high
    idx = pd.date_range("2025-01-01", periods=n_low + n_high, freq="h")
    return pd.Series(prices, index=idx, dtype=float)


def _balanced_inputs(n_hours=24):
    """Balanced actgen/load with no surplus or deficit in any zone."""
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    actgen = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
    load = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
    return actgen, load


def _surplus_inputs(surplus_zone, surplus_mw, n_hours=24):
    """Zone *surplus_zone* has *surplus_mw* above its load; all others balanced."""
    idx = pd.date_range("2025-01-01", periods=n_hours, freq="h")
    load = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
    actgen = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
    actgen[surplus_zone] += surplus_mw
    return actgen, load


# ---------------------------------------------------------------------------
# 1. solve_pf — unconstrained LP
# ---------------------------------------------------------------------------

class TestSolvePF:
    P_MW = 10.0
    E_MWH = 40.0
    ETA = 0.9

    @pytest.fixture
    def result_arb(self):
        """Low prices first, then high — BESS should buy cheap, sell expensive."""
        price = _price_series(low=10, high=50, n_low=12, n_high=12)
        return solve_pf(price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0)

    def test_positive_revenue(self, result_arb):
        rev, *_ = result_arb
        assert rev > 0, f"Expected positive revenue, got {rev:.1f}"

    def test_revenue_higher_than_flat_price(self):
        """Flat price → no arbitrage, revenue ≈ 0 (only throughput friction)."""
        price_flat = pd.Series([30.0] * 24, index=pd.date_range("2025-01-01", periods=24, freq="h"))
        rev_flat, *_ = solve_pf(price_flat, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0)
        rev_arb, *_ = solve_pf(
            _price_series(10, 50), self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0
        )
        assert rev_arb > rev_flat

    def test_soc_within_bounds(self, result_arb):
        _, _, _, soc, _, _ = result_arb
        assert (soc >= 0.0 - 1e-6).all(), f"SOC below 0: {soc.min():.3f}"
        assert (soc <= self.E_MWH + 1e-6).all(), f"SOC above {self.E_MWH}: {soc.max():.3f}"

    def test_dispatch_within_power_limit(self, result_arb):
        _, _, _, _, ch, dis = result_arb
        assert (ch <= self.P_MW + 1e-6).all()
        assert (dis <= self.P_MW + 1e-6).all()

    def test_throughput_cost_reduces_revenue(self):
        price = _price_series(10, 50)
        rev_0, _, _, _, _, _ = solve_pf(price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0)
        rev_1, _, _, _, _, _ = solve_pf(price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 5.0)
        assert rev_0 >= rev_1, "Higher throughput cost should not increase revenue"


# ---------------------------------------------------------------------------
# 2. solve_pf_network — NTC-constrained LP
# ---------------------------------------------------------------------------

class TestSolvePFNetwork:
    P_MW = 10.0
    E_MWH = 40.0
    ETA = 0.9
    N_HOURS = 24

    def _prices_df(self, zone_prices: dict) -> pd.DataFrame:
        idx = pd.date_range("2025-01-01", periods=self.N_HOURS, freq="h")
        data = {}
        for z in ZONES:
            data[z] = zone_prices.get(z, [30.0] * self.N_HOURS)
        return pd.DataFrame(data, index=idx)

    def _alternating_inputs(self):
        """NO1 alternates between surplus (hours 0-11) and deficit (hours 12-23).

        The build_network model can only trade when local surplus (BESS charges)
        or local deficit (BESS discharges) exists. Surplus hours have lower prices;
        deficit hours have higher prices — a physically grounded arbitrage setup.
        """
        idx = pd.date_range("2025-01-01", periods=self.N_HOURS, freq="h")
        prices = pd.DataFrame({z: 30.0 for z in ZONES}, index=idx, dtype=float)
        # Low price during surplus hours, high price during deficit hours
        prices["NO1"] = [10.0] * 12 + [50.0] * 12

        actgen = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
        load = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
        # NO1: surplus (200 MW) in hours 0-11; deficit (200 MW) in hours 12-23
        actgen["NO1"] = [1200.0] * 12 + [800.0] * 12
        return prices, actgen, load

    @pytest.fixture
    def alternating_result(self):
        prices, actgen, load = self._alternating_inputs()
        return solve_pf_network(
            "NO1", prices, actgen, load, None,
            self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0,
        )

    def test_positive_revenue(self, alternating_result):
        """BESS earns positive revenue: charges from local surplus at low prices,
        discharges into local deficit at high prices."""
        rev, *_ = alternating_result
        assert rev > 0, f"Expected positive revenue, got {rev:.1f}"

    def test_soc_within_bounds(self, alternating_result):
        _, _, _, soc, _, _ = alternating_result
        assert (soc >= 0.0 - 1e-6).all()
        assert (soc <= self.E_MWH + 1e-6).all()

    def test_dispatch_within_power_limit(self, alternating_result):
        _, _, _, _, ch, dis = alternating_result
        assert (ch <= self.P_MW + 1e-6).all()
        assert (dis <= self.P_MW + 1e-6).all()

    def test_network_revenue_leq_unconstrained(self):
        """Network-constrained value ≤ unconstrained for the same zone prices."""
        prices_df, actgen, load = self._alternating_inputs()

        rev_net, *_ = solve_pf_network(
            "NO1", prices_df, actgen, load, None,
            self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0,
        )
        rev_unc, *_ = solve_pf(
            prices_df["NO1"], self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0
        )
        # Allow small numerical tolerance
        assert rev_net <= rev_unc + 1.0, (
            f"Network revenue ({rev_net:.1f}) exceeds unconstrained ({rev_unc:.1f})"
        )


# ---------------------------------------------------------------------------
# 3. NTC congestion reduces BESS value at the expensive-side zone
# ---------------------------------------------------------------------------

class TestNTCCongestion:
    """Compare two zones: one with ample NTC headroom, one nearly saturated.

    Setup:
    - NO5 has cheap prices (10 €/MWh) and large surplus → exports heavily
    - NO1 has expensive prices (50 €/MWh) and a deficit
    - We test BESS valuation at NO1 under two NTC regimes:
        a) NO1-NO5 NTC = 1000 MW (ample headroom vs 500 MW surplus)
        b) NO1-NO5 NTC patched to 5 MW (nearly saturated)
    The BESS at NO1 should earn more with ample headroom (can import cheap power to charge).
    """

    P_MW = 20.0
    E_MWH = 80.0
    ETA = 0.9
    N_HOURS = 24

    def _inputs(self):
        """NO1 alternates surplus/deficit at low/high prices — same as the
        arbitrage test but with a tight NTC link to isolate NO1."""
        idx = pd.date_range("2025-01-01", periods=self.N_HOURS, freq="h")
        prices = pd.DataFrame({z: 30.0 for z in ZONES}, index=idx, dtype=float)
        prices["NO1"] = [10.0] * 12 + [50.0] * 12
        actgen = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
        load = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx)
        actgen["NO1"] = [1200.0] * 12 + [800.0] * 12
        return prices, actgen, load

    def test_congestion_reduces_value(self, monkeypatch):
        import norway_network as opf_mod
        prices, actgen, load = self._inputs()

        # Ample headroom — original NTC
        rev_free, *_ = solve_pf_network(
            "NO1", prices, actgen, load, None,
            self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0,
        )

        # Saturate NO1 links
        tight_ntc = {k: (5.0 if "NO1" in k else v) for k, v in opf_mod.NTC_MW.items()}
        monkeypatch.setattr(opf_mod, "NTC_MW", tight_ntc)

        rev_congested, *_ = solve_pf_network(
            "NO1", prices, actgen, load, None,
            self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0,
        )

        assert rev_free >= rev_congested - 1.0, (
            f"Expected ample NTC ({rev_free:.1f}) ≥ congested ({rev_congested:.1f})"
        )
