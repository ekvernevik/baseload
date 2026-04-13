"""Tests for norway_network.py.

Three behaviors under test:
  1. Network topology — correct buses, NTC link values, component counts
  2. Uncongested routing — surplus routes freely to deficit, no slack, zero shadow price
  3. Congested routing — NTC too small, slack fires at deficit zone, correct sign on shadow price
"""
import pandas as pd
import pytest

import pandas as _pd
_pd.options.future.infer_string = False

from norway_network import NTC_MW, SLACK_COST, ZONES, build_network, run_opf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshots(n: int = 3) -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01", periods=n, freq="h")


def _uniform(zones, value, n=3):
    """DataFrame with constant value for each zone."""
    idx = _snapshots(n)
    return pd.DataFrame({z: value for z in zones}, index=idx)


def _make_inputs(surplus_zone, deficit_zone, surplus_mw, deficit_mw,
                 other_gen=0.0, n=3):
    """
    Build minimal actgen / load DataFrames for a controlled scenario.

    surplus_zone generates surplus_mw more than its load.
    deficit_zone needs deficit_mw from external routing.
    All other zones are balanced (gen == load).
    """
    idx = _snapshots(n)
    load = _uniform(ZONES, 1000.0, n)
    actgen = _uniform(ZONES, 1000.0 + other_gen, n)   # balanced everywhere by default
    actgen[surplus_zone] = load[surplus_zone] + surplus_mw
    actgen[deficit_zone] = load[deficit_zone] - deficit_mw
    return actgen, load


# ---------------------------------------------------------------------------
# 1. Topology
# ---------------------------------------------------------------------------

class TestTopology:
    def test_buses(self):
        actgen, load = _make_inputs("NO5", "NO1", 500, 500)
        n, _ = build_network(actgen, load)
        assert set(n.buses.index) == set(ZONES)

    def test_link_names_match_ntc_keys(self):
        actgen, load = _make_inputs("NO5", "NO1", 500, 500)
        n, _ = build_network(actgen, load)
        assert set(n.links.index) == set(NTC_MW)

    def test_ntc_values_correct(self):
        actgen, load = _make_inputs("NO5", "NO1", 500, 500)
        n, _ = build_network(actgen, load)
        for pair, ntc in NTC_MW.items():
            assert n.links.loc[pair, "p_nom"] == ntc, f"{pair} p_nom mismatch"

    def test_links_bidirectional(self):
        actgen, load = _make_inputs("NO5", "NO1", 500, 500)
        n, _ = build_network(actgen, load)
        assert (n.links["p_min_pu"] == -1.0).all()

    def test_snapshot_count(self):
        actgen, load = _make_inputs("NO5", "NO1", 500, 500, n=24)
        n, _ = build_network(actgen, load)
        assert len(n.snapshots) == 24

    def test_external_balance_changes_common_idx(self):
        """Common index should be intersection of actgen, load, external_balance."""
        idx_full = _snapshots(6)
        idx_short = _snapshots(4)  # 4 hours only
        actgen = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx_full)
        load = pd.DataFrame({z: 1000.0 for z in ZONES}, index=idx_full)
        ext = pd.DataFrame({z: 0.0 for z in ZONES}, index=idx_short)
        n, common = build_network(actgen, load, external_balance=ext)
        assert len(common) == 4


# ---------------------------------------------------------------------------
# 2. Uncongested routing
# ---------------------------------------------------------------------------

class TestUncongestedRouting:
    """NO5 surplus → NO1 deficit, well within NTC limits (NTC=1000 MW, flow=200 MW)."""

    @pytest.fixture
    def result(self):
        actgen, load = _make_inputs("NO5", "NO1", surplus_mw=200, deficit_mw=200)
        n, common = build_network(actgen, load)
        shadow_prices, lmps = run_opf(n)
        return shadow_prices, lmps, n

    def test_no_slack(self, result):
        _, _, n = result
        slack_cols = [f"{z}_slack" for z in ZONES]
        available = [c for c in slack_cols if c in n.generators_t.p.columns]
        if available:
            total_slack = n.generators_t.p[available].sum().sum()
            assert total_slack < 1.0, f"Unexpected slack: {total_slack:.1f} MW"

    def test_shadow_prices_near_zero(self, result):
        shadow_prices, _, _ = result
        assert (shadow_prices.abs() < 1e-3).all().all(), \
            f"Expected zero shadow prices, got:\n{shadow_prices.describe()}"

    def test_lmps_have_correct_shape(self, result):
        _, lmps, _ = result
        assert set(lmps.columns) == set(ZONES)
        assert len(lmps) == 3

    def test_net_inflow_satisfies_deficit(self, result):
        """Net inflow to NO1 must equal its 200 MW deficit.

        We don't assert which specific link carries the flow — the LP has
        degenerate solutions (multiple minimum-cost routings) for the small
        1e-4 objective. Instead we verify nodal balance: sum of all flows
        entering NO1 minus sum leaving = 200 MW.
        """
        _, _, n = result
        p0 = n.links_t.p0
        net_into_no1 = 0.0
        for pair in NTC_MW:
            zone_a, zone_b = pair.split("-")
            if pair not in p0.columns:
                continue
            flow = p0[pair].mean()
            if zone_a == "NO1":
                net_into_no1 -= flow   # positive p0 leaves NO1
            if zone_b == "NO1":
                net_into_no1 += flow   # link arriving at NO1 (p1 = -p0)
        assert abs(net_into_no1 - 200.0) < 1.0, \
            f"Expected 200 MW net inflow to NO1, got {net_into_no1:.1f} MW"


# ---------------------------------------------------------------------------
# 3. Congested routing
# ---------------------------------------------------------------------------

class TestCongestedRouting:
    """NO5 surplus → NO1 deficit, but ALL NTC links into NO1 reduced to 1 MW.
    Slack must fire at NO1; shadow price on NO1-adjacent links must be SLACK_COST."""

    @pytest.fixture
    def result_congested(self, monkeypatch):
        # Patch NTC_MW to make all links into NO1 trivially small
        tiny_ntc = {k: (1.0 if "NO1" in k else v) for k, v in NTC_MW.items()}
        monkeypatch.setattr("norway_network.NTC_MW", tiny_ntc)

        actgen, load = _make_inputs("NO5", "NO1", surplus_mw=500, deficit_mw=500)
        n, common = build_network(actgen, load)
        shadow_prices, lmps = run_opf(n)
        return shadow_prices, lmps, n

    def test_slack_fires(self, result_congested):
        _, _, n = result_congested
        slack_cols = [f"{z}_slack" for z in ZONES]
        available = [c for c in slack_cols if c in n.generators_t.p.columns]
        assert available, "Slack generators not tracked"
        no1_slack = n.generators_t.p.get("NO1_slack", pd.Series(0))
        assert no1_slack.sum() > 1.0, "Expected NO1 slack to fire"

    def test_shadow_price_sign_no1_links(self, result_congested):
        """NO1 is the deficit zone — links ending at NO1 should show NO1 more expensive.
        Shadow price = LMP[zone_a] - LMP[zone_b]. For NO1-NO5: zone_a=NO1, zone_b=NO5.
        NO1 expensive → LMP[NO1] > LMP[NO5] → shadow_price > 0."""
        shadow_prices, _, _ = result_congested
        for pair in ["NO1-NO5", "NO1-NO2", "NO1-NO3"]:
            sp = shadow_prices[pair].mean()
            assert sp > 0, \
                f"{pair}: expected positive shadow price (NO1 deficit), got {sp:.1f}"

    def test_shadow_price_magnitude(self, result_congested):
        """When only slack can serve NO1, LMP[NO1] = SLACK_COST → shadow ≈ SLACK_COST."""
        shadow_prices, _, _ = result_congested
        sp = shadow_prices["NO1-NO5"].mean()
        assert abs(sp - SLACK_COST) < 1.0, \
            f"Expected shadow ≈ {SLACK_COST}, got {sp:.1f}"

    def test_uncongested_link_zero_shadow(self, result_congested):
        """NO3-NO4 has no NO1 involvement — should be zero shadow price."""
        shadow_prices, _, _ = result_congested
        sp = shadow_prices["NO3-NO4"].abs().mean()
        assert sp < 1e-3, f"NO3-NO4 should be uncongested, got {sp:.1f}"


# ---------------------------------------------------------------------------
# 4. External balance integration
# ---------------------------------------------------------------------------

class TestExternalBalance:
    def test_import_reduces_deficit(self):
        """External import at NO1 should reduce its deficit, requiring less internal routing."""
        actgen, load = _make_inputs("NO5", "NO1", surplus_mw=0, deficit_mw=500)
        ext = pd.DataFrame({z: (500.0 if z == "NO1" else 0.0) for z in ZONES},
                           index=_snapshots())
        n, _ = build_network(actgen, load, external_balance=ext)
        shadow_prices, lmps = run_opf(n)
        # With external import covering the deficit, no slack needed
        slack_cols = [c for c in [f"{z}_slack" for z in ZONES]
                      if c in n.generators_t.p.columns]
        if slack_cols:
            total_slack = n.generators_t.p[slack_cols].sum().sum()
            assert total_slack < 1.0, f"Unexpected slack with full import cover: {total_slack:.1f}"
