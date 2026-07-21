"""Tests for issue #20 — NTC calibration against observed congestion."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import yaml

from calibrate_ntc import calibrate_ntc, calibrate_pair, emit_config_block
from norway_network import network_params_from_config


def _congested_link(true_ntc: float = 800.0, periods: int = 8760, seed: int = 11):
    """Synthetic link where prices separate exactly when flow hits the cap."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=periods, freq="h", tz="UTC")
    desired = rng.gamma(shape=4, scale=150, size=periods)      # desired transfer
    flow = np.minimum(desired, true_ntc)
    congested = desired > true_ntc
    spread = np.where(congested, rng.uniform(5, 60, periods), rng.normal(0, 0.2, periods))
    return (
        pd.Series(spread, index=idx),
        pd.Series(flow, index=idx),
        congested.mean(),
    )


class TestCalibratePair:
    def test_recovers_true_ntc(self):
        spread, flow, f_true = _congested_link(true_ntc=800.0)
        result = calibrate_pair(spread, flow)
        assert result["ntc_mw"] == pytest.approx(800.0, rel=0.02)
        assert result["observed_sep_freq"] == pytest.approx(f_true, abs=0.01)

    def test_binding_frequency_reproduced_within_tolerance(self):
        spread, flow, _ = _congested_link(true_ntc=600.0)
        result = calibrate_pair(spread, flow)
        # Done-when criterion: modeled binding frequency matches observed
        # separation frequency within a documented tolerance.
        assert result["abs_error_pp"] <= 2.0

    def test_never_binding_link(self):
        idx = pd.date_range("2024-01-01", periods=1000, freq="h", tz="UTC")
        spread = pd.Series(0.0, index=idx)
        flow = pd.Series(np.random.default_rng(0).uniform(0, 400, 1000), index=idx)
        result = calibrate_pair(spread, flow)
        assert result["observed_sep_freq"] == 0.0
        assert result["ntc_mw"] > flow.abs().max()
        assert result["modeled_binding_freq"] == 0.0

    def test_too_little_data_raises(self):
        idx = pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC")
        with pytest.raises(ValueError, match="Too little"):
            calibrate_pair(pd.Series(0.0, index=idx), pd.Series(1.0, index=idx))


class TestCalibrateTable:
    def _fixture(self):
        spread_a, flow_a, _ = _congested_link(true_ntc=800.0, seed=1)
        spread_b, flow_b, _ = _congested_link(true_ntc=300.0, seed=2)
        prices = pd.DataFrame({
            "NO1": 50.0 + spread_a + 0.5 * spread_b,
            "NO2": 50.0 + 0.5 * spread_b,
            "NO5": 50.0,
        })
        prices["NO2"] = prices["NO1"] - spread_a           # NO1-NO2 spread = spread_a
        prices["NO5"] = prices["NO1"] - spread_b           # NO1-NO5 spread = spread_b
        flows = pd.DataFrame({"NO1-NO2": flow_a, "NO1-NO5": flow_b})
        return prices, flows

    def test_multi_pair_report(self):
        prices, flows = self._fixture()
        report = calibrate_ntc(prices, flows, ["NO1-NO2", "NO1-NO5"])
        assert set(report["pair"]) == {"NO1-NO2", "NO1-NO5"}
        by_pair = report.set_index("pair")
        assert by_pair.loc["NO1-NO2", "ntc_mw"] == pytest.approx(800.0, rel=0.05)
        assert by_pair.loc["NO1-NO5", "ntc_mw"] == pytest.approx(300.0, rel=0.05)
        assert (report["within_tolerance"] == 1.0).all()

    def test_missing_pair_skipped(self):
        prices, flows = self._fixture()
        report = calibrate_ntc(prices, flows, ["NO1-NO2", "NO3-NO4"])
        assert list(report["pair"]) == ["NO1-NO2"]

    def test_emitted_yaml_is_consumable_by_network(self, tmp_path):
        prices, flows = self._fixture()
        report = calibrate_ntc(prices, flows, ["NO1-NO2", "NO1-NO5"])
        provenance = {
            "generated": "2026-07-21T00:00Z", "data_start": "2024-01-01",
            "data_end": "2024-12-31", "sep_threshold": 1.0, "window_years": 1.0,
        }
        out = tmp_path / "ntc_calibrated.yaml"
        emit_config_block(report, provenance, out)

        cfg = yaml.safe_load(out.read_text())
        zones, ntc = network_params_from_config(cfg)
        assert ntc["NO1-NO2"] == pytest.approx(800.0, rel=0.05)
        spec = cfg["network"]["interconnectors"]["NO1-NO2"]
        assert spec["calibrated"] is True
        assert "window 2024-01-01..2024-12-31" in spec["source"]
