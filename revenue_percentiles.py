#!/usr/bin/env python3
"""Script 8: BESS P50/P90 revenue methodology (issue #21).

Maps the price-scenario ensemble (issue #18) to a lender-facing distribution
of annual BESS revenue per zone, and reports P50/P90 (plus P10 and mean).
The full methodology, conventions, and assumptions are documented in
docs/p50_p90_methodology.md — this script is its reproducible implementation.

Pipeline per zone:
1. Fit the probabilistic forecaster on the zone's price history.
2. Draw `n_scenarios` coherent price paths over the evaluation window
   (day-block bootstrap around P50 — preserves intra-day and day-to-day
   correlation structure; see methodology doc §3).
3. For each scenario, dispatch the configured BESS with a perfect-foresight LP
   *within the scenario* and record the revenue.
4. Convert scenario revenues to the realistic (causal-dispatch) level with the
   realism gap measured by the stochastic-MPC benchmark run (issue #19):
   valuation_rh's `gap_vs_perfect_rh_pct` when available, else
   `p50p90.default_realism_gap_pct` from config.
5. Annualise and report percentiles. Lender convention: P90 is the annual
   revenue exceeded with 90 % probability (the 10th percentile of the
   distribution); P50 is the median.

Output: artifacts/tables/revenue_p50_p90.parquet (+ .csv).
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from baseload.io import read_prices, write_parquet
from baseload.pipeline_utils import ensure_dirs, index_dt_hours, load_config, resolution_freq
from baseload.zones import zones_from_cfg

from bess_valuation_rh import solve_window
from price_forecast import QuantileForecaster


def scenario_revenue(
    path: pd.Series, p_mw: float, e_mwh: float, eta: float
) -> float:
    """Perfect-foresight dispatch revenue for one scenario price path."""
    dt = index_dt_hours(path.index)
    # cyclic: terminal SOC returns to the start level, so revenue reflects
    # arbitrage only — never a one-off sale of the initial charge.
    ch_plan, dis_plan, _ = solve_window(path, p_mw, e_mwh, eta, soc0=0.5 * e_mwh, cyclic=True)
    ch = np.asarray(ch_plan)
    dis = np.asarray(dis_plan)
    return float(dt * (path.values * (dis - ch)).sum())


def annual_revenue_distribution(
    price: pd.Series,
    p_mw: float,
    e_mwh: float,
    eta: float,
    n_scenarios: int = 50,
    eval_days: int = 30,
    realism_gap_pct: float = 15.0,
    seed: int = 42,
    forecaster_kwargs: dict | None = None,
) -> tuple[np.ndarray, dict]:
    """Distribution of annualised, realism-adjusted revenue for one zone.

    Returns (revenues_eur_per_year, meta). Scenario dispatch is solved in
    day-sized LP chunks for tractability; chunk boundaries carry SOC via the
    0.5·E reset, which is a documented approximation (methodology doc §6).
    """
    price = price.dropna()
    dt = index_dt_hours(price.index)
    ppd = max(int(round(24.0 / dt)), 1)
    fkw = forecaster_kwargs or {}

    # Hold out the evaluation window; fit the forecaster on everything before it.
    eval_index = price.index[-eval_days * ppd:]
    history = price[price.index < eval_index[0]]
    forecaster = QuantileForecaster(seed=seed, **fkw).fit(history)
    scenarios = forecaster.scenarios(history, eval_index, n_scenarios=n_scenarios, seed=seed)

    eval_hours = len(eval_index) * dt
    annualisation = 8760.0 / eval_hours
    realism = 1.0 - realism_gap_pct / 100.0

    revenues = np.empty(n_scenarios)
    for s in range(n_scenarios):
        scenario_path = scenarios.iloc[:, s]
        rev = 0.0
        for start in range(0, len(scenario_path), ppd):
            chunk = scenario_path.iloc[start:start + ppd]
            if len(chunk) < 2:
                continue
            rev += scenario_revenue(chunk, p_mw, e_mwh, eta)
        revenues[s] = rev * annualisation * realism

    meta = {
        "eval_hours": float(eval_hours),
        "realism_gap_pct": float(realism_gap_pct),
        "n_scenarios": float(n_scenarios),
    }
    return revenues, meta


def percentile_report(revenues: np.ndarray, p_mw: float, meta: dict, zone: str) -> dict:
    """Lender convention: P90 = exceeded with 90 % probability = 10th percentile."""
    p50 = float(np.percentile(revenues, 50))
    p90 = float(np.percentile(revenues, 10))
    p10 = float(np.percentile(revenues, 90))
    return {
        "zone": zone,
        "p50_eur_yr": p50,
        "p90_eur_yr": p90,
        "p10_eur_yr": p10,
        "mean_eur_yr": float(revenues.mean()),
        "p50_eur_per_kw_yr": p50 / (p_mw * 1000),
        "p90_eur_per_kw_yr": p90 / (p_mw * 1000),
        **meta,
    }


def realism_gap_from_valuation_rh(paths, zone: str, default_pct: float) -> float:
    """Prefer the measured stochastic-vs-perfect gap (issue #19) when present."""
    rh_path = paths["tables"] / "valuation_rh.parquet"
    if not rh_path.exists():
        return default_pct
    rh = pd.read_parquet(rh_path)
    if "gap_vs_perfect_rh_pct" not in rh.columns or zone not in set(rh["zone"]):
        return default_pct
    row = rh.set_index("zone").loc[zone]
    if str(row.get("mode", "")) != "stochastic":
        return default_pct
    return float(np.clip(row["gap_vs_perfect_rh_pct"], 0.0, 100.0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    zones = zones_from_cfg(cfg)
    freq = resolution_freq(cfg)
    prices = read_prices(paths, zones=zones, freq=freq)

    bcfg = cfg.get("bess", {})
    p_mw = float(bcfg.get("p_mw", 50))
    e_mwh = float(bcfg.get("e_mwh", 200))
    eta = float(bcfg.get("eta", 0.9))

    pcfg = cfg.get("p50p90", {}) or {}
    n_scenarios = int(pcfg.get("n_scenarios", 50))
    eval_days = int(pcfg.get("eval_days", 60))
    default_gap = float(pcfg.get("default_realism_gap_pct", 15.0))
    seed = int(pcfg.get("seed", 42))

    rows = []
    for zone in sorted(prices.columns):
        gap = realism_gap_from_valuation_rh(paths, zone, default_gap)
        print(f"  {zone}: {n_scenarios} scenarios over {eval_days}d, realism gap {gap:.1f}%...", flush=True)
        revenues, meta = annual_revenue_distribution(
            prices[zone].ffill(), p_mw, e_mwh, eta,
            n_scenarios=n_scenarios, eval_days=eval_days,
            realism_gap_pct=gap, seed=seed,
        )
        rows.append(percentile_report(revenues, p_mw, meta, zone))

    report = pd.DataFrame(rows)
    write_parquet(report, paths["tables"] / "revenue_p50_p90.parquet",
                  schema_name="revenue_p50_p90", also_csv=True)
    print("\nP50/P90 annual revenue (lender convention: P90 = exceeded 90% of years):")
    print(report[["zone", "p50_eur_yr", "p90_eur_yr", "p50_eur_per_kw_yr", "p90_eur_per_kw_yr"]].to_string(index=False))


if __name__ == "__main__":
    main()
