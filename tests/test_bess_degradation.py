"""Tests for baseload/bess_degradation.py and its integration into solve_pf.

Behaviors under test:
  1. capacity_retention  — decreases with age and cycling, floors at 0
  2. chemistry contrast   — LFP degrades slower and cycles harder than NMC
  3. thermal derating     — cold ambient reduces efficiency, never below the floor
  4. end-of-life          — reached_eol trips at the configured threshold
  5. cycling limit        — solve_pf's max_cycles_per_day constraint actually binds
  6. multi-year valuation — solve_pf_multiyear degrades capacity/revenue over life
"""
import pandas as pd
import pytest

from baseload.bess_degradation import (
    LFP,
    NMC,
    CHEMISTRY_PRESETS,
    get_chemistry,
    capacity_retention,
    thermal_efficiency_multiplier,
    reached_eol,
    max_efc_for_horizon,
)
from bess_valuation_pf import solve_pf, solve_pf_multiyear


# ---------------------------------------------------------------------------
# 1. capacity_retention
# ---------------------------------------------------------------------------

class TestCapacityRetention:
    def test_no_age_no_cycling_is_full_capacity(self):
        assert capacity_retention(0.0, 0.0, LFP) == pytest.approx(1.0)

    def test_decreases_with_age(self):
        assert capacity_retention(5.0, 0.0, LFP) < capacity_retention(1.0, 0.0, LFP)

    def test_decreases_with_cycling(self):
        assert capacity_retention(1.0, 5000.0, LFP) < capacity_retention(1.0, 0.0, LFP)

    def test_floors_at_zero(self):
        assert capacity_retention(1000.0, 1_000_000.0, LFP) == 0.0


# ---------------------------------------------------------------------------
# 2. Chemistry contrast — LFP should outlast NMC under identical use
# ---------------------------------------------------------------------------

class TestChemistryContrast:
    def test_lfp_retains_more_capacity_than_nmc(self):
        age, efc = 5.0, 3000.0
        assert capacity_retention(age, efc, LFP) > capacity_retention(age, efc, NMC)

    def test_lfp_allows_more_annual_cycling_than_nmc(self):
        assert max_efc_for_horizon(LFP, 1.0) > max_efc_for_horizon(NMC, 1.0)

    def test_get_chemistry_resolves_name_case_insensitively(self):
        assert get_chemistry("lfp") is LFP
        assert get_chemistry("NMC") is NMC

    def test_get_chemistry_rejects_unknown_name(self):
        with pytest.raises(ValueError):
            get_chemistry("NCA")

    def test_get_chemistry_passes_through_chemistry_params(self):
        assert get_chemistry(LFP) is LFP

    def test_presets_registry_has_both_chemistries(self):
        assert set(CHEMISTRY_PRESETS) == {"LFP", "NMC"}


# ---------------------------------------------------------------------------
# 3. Thermal derating
# ---------------------------------------------------------------------------

class TestThermalDerating:
    def test_no_derate_at_or_above_threshold(self):
        assert thermal_efficiency_multiplier(10.0, LFP) == 1.0
        assert thermal_efficiency_multiplier(LFP.cold_derate_temp_c, LFP) == 1.0

    def test_derates_below_threshold(self):
        assert thermal_efficiency_multiplier(-10.0, LFP) < 1.0

    def test_colder_derates_more(self):
        assert thermal_efficiency_multiplier(-20.0, LFP) < thermal_efficiency_multiplier(-5.0, LFP)

    def test_floored_for_extreme_cold(self):
        assert thermal_efficiency_multiplier(-1000.0, LFP) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 4. End-of-life
# ---------------------------------------------------------------------------

class TestEndOfLife:
    def test_not_eol_at_full_capacity(self):
        assert not reached_eol(1.0, LFP)

    def test_eol_at_threshold(self):
        assert reached_eol(LFP.eol_retention, LFP)

    def test_eol_below_threshold(self):
        assert reached_eol(LFP.eol_retention - 0.01, LFP)


# ---------------------------------------------------------------------------
# 5. Cycling limit binds in the LP
# ---------------------------------------------------------------------------

class TestCyclingLimitConstraint:
    P_MW = 10.0
    E_MWH = 40.0
    ETA = 0.9

    def _price_series(self, n_days=10):
        """Daily low/high alternation — plenty of arbitrage opportunity to cycle on."""
        prices = ([10.0] * 12 + [50.0] * 12) * n_days
        idx = pd.date_range("2025-01-01", periods=len(prices), freq="h")
        return pd.Series(prices, index=idx, dtype=float)

    def test_unconstrained_cycles_exceed_tight_cap(self):
        price = self._price_series()
        _, _, cycles_free, *_ = solve_pf(price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0)
        assert cycles_free > 2.0, "Test setup should offer more than 2 cycles of arbitrage headroom"

    def test_max_cycles_caps_throughput(self):
        price = self._price_series()
        n_days = 10
        total_cap = 2.0
        _, _, cycles_capped, *_ = solve_pf(
            price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0,
            max_cycles_per_day=total_cap / n_days,
        )
        assert cycles_capped <= total_cap + 1e-6

    def test_capped_revenue_leq_uncapped(self):
        price = self._price_series()
        n_days = 10
        rev_free, *_ = solve_pf(price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0)
        rev_capped, *_ = solve_pf(
            price, self.P_MW, self.E_MWH, self.ETA, 0.0, 1.0, 0.0,
            max_cycles_per_day=1.0 / n_days,
        )
        assert rev_capped <= rev_free + 1e-6


# ---------------------------------------------------------------------------
# 6. Multi-year valuation
# ---------------------------------------------------------------------------

class TestSolvePFMultiyear:
    P_MW = 10.0
    E_MWH_NOMINAL = 40.0

    def _price_series(self):
        prices = ([10.0] * 12 + [50.0] * 12) * 15  # 15 alternating days ~ 360h, cheap to solve
        idx = pd.date_range("2025-01-01", periods=len(prices), freq="h")
        return pd.Series(prices, index=idx, dtype=float)

    def test_capacity_retention_declines_year_over_year(self):
        out = solve_pf_multiyear(
            self._price_series(), self.P_MW, self.E_MWH_NOMINAL, "LFP",
            0.0, 1.0, 0.0, asset_life_years=5, discount_rate=0.08,
        )
        assert (out["capacity_retention_pct"].diff().dropna() <= 0).all()

    def test_effective_capacity_never_exceeds_nominal(self):
        out = solve_pf_multiyear(
            self._price_series(), self.P_MW, self.E_MWH_NOMINAL, "LFP",
            0.0, 1.0, 0.0, asset_life_years=5, discount_rate=0.08,
        )
        assert (out["e_mwh_effective"] <= self.E_MWH_NOMINAL + 1e-6).all()

    def test_discounted_revenue_leq_nominal(self):
        out = solve_pf_multiyear(
            self._price_series(), self.P_MW, self.E_MWH_NOMINAL, "LFP",
            0.0, 1.0, 0.0, asset_life_years=5, discount_rate=0.08,
        )
        assert (out["revenue_discounted_eur"] <= out["revenue_nominal_eur"] + 1e-6).all()

    def test_stops_early_at_end_of_life(self):
        """A short, cheap horizon that never earns much still must not run past a
        requested life of, say, 500 years without hitting EOL first — calendar
        fade alone guarantees termination well before then."""
        out = solve_pf_multiyear(
            self._price_series(), self.P_MW, self.E_MWH_NOMINAL, "LFP",
            0.0, 1.0, 0.0, asset_life_years=500, discount_rate=0.08,
        )
        assert len(out) < 500
