#!/usr/bin/env python3
"""Script 7: rolling-horizon BESS valuation — stochastic MPC with CVaR.

Issue #19: the original rolling horizon fed *realised* prices into every
lookahead window, so it was perfect foresight in disguise (penalty 0.13-0.23%).
This version is causal: at each step the optimiser sees only

  - the realised price history up to now (used to fit/update the forecaster), and
  - a scenario ensemble for the lookahead window drawn from the probabilistic
    day-ahead forecast (price_forecast.QuantileForecaster, issue #18).

The window LP is a two-stage stochastic program: the first-period action is
shared across scenarios (it is committed now), later periods recourse per
scenario. Downside is managed with a CVaR term (Rockafellar-Uryasev):

    maximize  E[revenue] - cvar_weight * CVaR_alpha[-revenue]

Perfect-foresight rolling horizon is retained ONLY as a benchmark; the output
table reports the realistic-vs-perfect-foresight gap per zone.

Config (rolling:):
  lookahead_h: 24      step_h: 1
  mode: stochastic     # stochastic | perfect (benchmark-only run)
  n_scenarios: 20      cvar_alpha: 0.95      cvar_weight: 0.2
  warmup_days: 28      # history reserved for fitting the first forecaster
  refit_every_days: 7
"""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from baseload.pipeline_utils import index_dt_hours, init_pipeline, load_config
from baseload.io import read_prices, write_valuation_rh

from price_forecast import QuantileForecaster

try:
    import pulp
except ImportError as exc:  # pragma: no cover
    raise ImportError("PuLP is required for bess valuation scripts. Install with `pip install pulp`.") from exc


# ---------------------------------------------------------------------------
# Window LPs
# ---------------------------------------------------------------------------

def solve_window(price: pd.Series, p_mw: float, e_mwh: float, eta: float, soc0: float,
                 cyclic: bool = False):
    """Deterministic lookahead window; returns the full planned trajectory.

    Used for the perfect-foresight benchmark (realised prices in the window)
    — kept ONLY as a benchmark, not as the headline valuation.

    ``cyclic=True`` forces terminal SOC back to *soc0* — required when the
    window is used as a standalone valuation chunk (revenue_percentiles.py),
    otherwise the LP monetises the initial charge as free revenue.
    """
    t = range(len(price))
    dt = index_dt_hours(price.index)
    model = pulp.LpProblem("bess_rh", pulp.LpMaximize)
    ch = pulp.LpVariable.dicts("ch", t, lowBound=0, upBound=p_mw)
    dis = pulp.LpVariable.dicts("dis", t, lowBound=0, upBound=p_mw)
    soc = pulp.LpVariable.dicts("soc", t, lowBound=0.1 * e_mwh, upBound=0.9 * e_mwh)

    model += pulp.lpSum([dt * price.iloc[i] * (dis[i] - ch[i]) for i in t])

    for i in t:
        prev = soc0 if i == 0 else soc[i - 1]
        model += soc[i] == prev + dt * (eta * ch[i] - dis[i] / eta)

    if cyclic:
        model += soc[len(price) - 1] == soc0, "cyclic_soc"

    model.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[model.status] != "Optimal":
        raise RuntimeError("RH optimization failed")

    ch_plan = [ch[i].value() for i in t]
    dis_plan = [dis[i].value() for i in t]
    soc_plan = [soc[i].value() for i in t]
    return ch_plan, dis_plan, soc_plan


def solve_window_stochastic(
    scenarios: pd.DataFrame,
    p_mw: float,
    e_mwh: float,
    eta: float,
    soc0: float,
    cvar_alpha: float = 0.95,
    cvar_weight: float = 0.2,
    probs: np.ndarray | None = None,
) -> tuple[float, float, float]:
    """Two-stage stochastic window LP over a scenario ensemble.

    *scenarios*: (T, S) DataFrame — one price path per column. The first-period
    charge/discharge is a single shared decision (committed now); periods
    t >= 1 are scenario-specific recourse. Objective:

        max  sum_s p_s * rev_s  -  cvar_weight * ( eta + 1/(1-alpha) * sum_s p_s * z_s )
        s.t. z_s >= -rev_s - eta,  z_s >= 0        (CVaR of the loss -rev)

    Returns (ch_plan, dis_plan, soc_plan) over the window; period 0 is the
    shared committed action, periods >= 1 are the mean recourse plan.
    """
    T, S = scenarios.shape
    dt = index_dt_hours(scenarios.index)
    if probs is None:
        probs = np.full(S, 1.0 / S)

    model = pulp.LpProblem("bess_rh_stoch", pulp.LpMaximize)

    ch0 = pulp.LpVariable("ch0", lowBound=0, upBound=p_mw)
    dis0 = pulp.LpVariable("dis0", lowBound=0, upBound=p_mw)

    ch = pulp.LpVariable.dicts("ch", [(s, i) for s in range(S) for i in range(1, T)], lowBound=0, upBound=p_mw)
    dis = pulp.LpVariable.dicts("dis", [(s, i) for s in range(S) for i in range(1, T)], lowBound=0, upBound=p_mw)
    soc = pulp.LpVariable.dicts("soc", [(s, i) for s in range(S) for i in range(T)],
                                lowBound=0.1 * e_mwh, upBound=0.9 * e_mwh)

    # CVaR auxiliaries.
    eta_var = pulp.LpVariable("cvar_eta")          # value-at-risk level (free)
    z = pulp.LpVariable.dicts("z", range(S), lowBound=0)

    revenues = []
    for s in range(S):
        path = scenarios.iloc[:, s].values
        rev_terms = [dt * path[0] * (dis0 - ch0)]
        model += soc[(s, 0)] == soc0 + dt * (eta * ch0 - dis0 / eta)
        for i in range(1, T):
            rev_terms.append(dt * path[i] * (dis[(s, i)] - ch[(s, i)]))
            model += soc[(s, i)] == soc[(s, i - 1)] + dt * (eta * ch[(s, i)] - dis[(s, i)] / eta)
        rev_s = pulp.lpSum(rev_terms)
        revenues.append(rev_s)
        model += z[s] >= -rev_s - eta_var

    expected = pulp.lpSum([probs[s] * revenues[s] for s in range(S)])
    cvar = eta_var + (1.0 / (1.0 - cvar_alpha)) * pulp.lpSum([probs[s] * z[s] for s in range(S)])
    model += expected - cvar_weight * cvar

    model.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[model.status] != "Optimal":
        raise RuntimeError("Stochastic RH window failed")

    c0, d0 = ch0.value(), dis0.value()

    # Committed plan beyond period 0: probability-weighted mean of the scenario
    # recourse actions. Feasibility is preserved — SOC bounds and the energy
    # balance are linear, so a convex combination of feasible plans is feasible.
    ch_plan, dis_plan, soc_plan = [c0], [d0], []
    soc_prev = soc0 + dt * (eta * c0 - d0 / eta)
    soc_plan.append(soc_prev)
    for i in range(1, T):
        c_i = float(sum(probs[s] * ch[(s, i)].value() for s in range(S)))
        d_i = float(sum(probs[s] * dis[(s, i)].value() for s in range(S)))
        soc_prev = soc_prev + dt * (eta * c_i - d_i / eta)
        ch_plan.append(c_i)
        dis_plan.append(d_i)
        soc_plan.append(soc_prev)
    return ch_plan, dis_plan, soc_plan


# ---------------------------------------------------------------------------
# Rolling simulations
# ---------------------------------------------------------------------------

def run_rh(price: pd.Series, p_mw: float, e_mwh: float, eta: float, lookahead: int, step: int):
    """Perfect-foresight receding horizon (benchmark only).

    Feeds realised prices into each window — an upper bound a real operator
    cannot achieve. Kept to quantify the realistic-vs-perfect gap.
    """
    dt = index_dt_hours(price.index)
    soc = 0.5 * e_mwh
    ch, dis, soc_trace = [], [], []
    idx = list(price.index)

    for i in range(0, len(idx), step):
        j = min(i + lookahead, len(idx))
        win = price.iloc[i:j]
        if win.empty:
            break
        ch_plan, dis_plan, soc_plan = solve_window(win, p_mw, e_mwh, eta, soc)

        # Commit the first `step` periods of the plan (physically consistent:
        # SOC follows the committed actions, not a frozen first-period value).
        commit = min(step, len(idx) - i, len(ch_plan))
        ch.extend(ch_plan[:commit])
        dis.extend(dis_plan[:commit])
        soc_trace.extend(soc_plan[:commit])
        soc = soc_plan[commit - 1]

    ch_s = pd.Series(ch[: len(price)], index=price.index)
    dis_s = pd.Series(dis[: len(price)], index=price.index)
    soc_s = pd.Series(soc_trace[: len(price)], index=price.index)
    rev = dt * (price * (dis_s - ch_s)).sum()
    return rev, soc_s


def run_rh_stochastic(
    price: pd.Series,
    p_mw: float,
    e_mwh: float,
    eta: float,
    lookahead: int,
    step: int,
    n_scenarios: int = 20,
    cvar_alpha: float = 0.95,
    cvar_weight: float = 0.2,
    warmup_days: int = 28,
    refit_every_days: int = 7,
    seed: int = 42,
    forecaster_kwargs: dict | None = None,
) -> tuple[float, pd.Series, pd.DatetimeIndex]:
    """Causal stochastic MPC over the post-warmup part of *price*.

    The first `warmup_days` are used only as forecaster training history.
    At each step: scenarios are drawn from the forecast conditioned on realised
    history (never on future prices), the stochastic window LP is solved, the
    first-period action committed, and P&L settles at the realised price.

    Returns (revenue, soc_series, evaluation_index).
    """
    price = price.dropna()
    dt = index_dt_hours(price.index)
    ppd = max(int(round(24.0 / dt)), 1)

    if len(price) <= (warmup_days + 1) * ppd:
        raise ValueError(
            f"Need more than {warmup_days} days of prices for warmup, got {len(price) / ppd:.1f}."
        )

    eval_index = price.index[warmup_days * ppd:]
    fkw = forecaster_kwargs or {}

    soc = 0.5 * e_mwh
    ch, dis, soc_trace = [], [], []
    forecaster: QuantileForecaster | None = None
    last_fit_day = None

    for i in range(0, len(eval_index), step):
        now = eval_index[i]
        history = price[price.index < now]

        day = now.normalize()
        if forecaster is None or (day - last_fit_day).days >= refit_every_days:
            forecaster = QuantileForecaster(seed=seed, **fkw).fit(history)
            last_fit_day = day

        j = min(i + lookahead, len(eval_index))
        window_index = eval_index[i:j]
        scen = forecaster.scenarios(history, window_index, n_scenarios=n_scenarios, seed=seed + i)

        ch_plan, dis_plan, soc_plan = solve_window_stochastic(
            scen, p_mw, e_mwh, eta, soc,
            cvar_alpha=cvar_alpha, cvar_weight=cvar_weight,
        )

        commit = min(step, len(eval_index) - i, len(ch_plan))
        ch.extend(ch_plan[:commit])
        dis.extend(dis_plan[:commit])
        soc_trace.extend(soc_plan[:commit])
        soc = soc_plan[commit - 1]

    ch_s = pd.Series(ch[: len(eval_index)], index=eval_index)
    dis_s = pd.Series(dis[: len(eval_index)], index=eval_index)
    soc_s = pd.Series(soc_trace[: len(eval_index)], index=eval_index)
    realised = price.reindex(eval_index)
    rev = dt * (realised * (dis_s - ch_s)).sum()   # settle at realised prices
    return rev, soc_s, eval_index


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = init_pipeline(cfg)
    prices = read_prices(paths)  # validates against PRICES_SCHEMA on load

    bcfg = cfg.get("bess", {})
    p_mw = float(bcfg.get("p_mw", 50))
    e_mwh = float(bcfg.get("e_mwh", 200))
    eta = float(bcfg.get("eta", 0.9))
    rh_cfg = cfg.get("rolling", {})
    lookahead = int(rh_cfg.get("lookahead_h", 24))
    step = int(rh_cfg.get("step_h", 1))
    mode = str(rh_cfg.get("mode", "stochastic")).lower()
    n_scenarios = int(rh_cfg.get("n_scenarios", 20))
    cvar_alpha = float(rh_cfg.get("cvar_alpha", 0.95))
    cvar_weight = float(rh_cfg.get("cvar_weight", 0.2))
    warmup_days = int(rh_cfg.get("warmup_days", 28))
    refit_every_days = int(rh_cfg.get("refit_every_days", 7))

    from bess_valuation_pf import solve_pf  # local import: pulls pypsa, only needed here

    rows = []
    soc_store = {}
    for zone in sorted(prices.columns):
        zone_price = prices[zone].ffill().fillna(0)

        if mode == "stochastic":
            print(f"  {zone}: stochastic MPC ({n_scenarios} scenarios, CVaR a={cvar_alpha})...", flush=True)
            rev, soc, eval_index = run_rh_stochastic(
                zone_price, p_mw, e_mwh, eta, lookahead, step,
                n_scenarios=n_scenarios, cvar_alpha=cvar_alpha, cvar_weight=cvar_weight,
                warmup_days=warmup_days, refit_every_days=refit_every_days,
            )
            # Benchmarks over the SAME evaluation window for a fair gap.
            eval_price = zone_price.reindex(eval_index)
            print(f"  {zone}: perfect-foresight benchmarks...", flush=True)
            pf_rev, *_ = solve_pf(eval_price, p_mw, e_mwh, eta, 0.1, 0.9, 0.0)
            rev_perfect_eval, _ = run_rh(eval_price, p_mw, e_mwh, eta, lookahead, step)
        else:  # benchmark-only run (legacy perfect-foresight behaviour, explicit)
            print(f"  {zone}: perfect-foresight RH (benchmark mode)...", flush=True)
            rev, soc = run_rh(zone_price, p_mw, e_mwh, eta, lookahead, step)
            eval_index = zone_price.index
            pf_rev, *_ = solve_pf(zone_price, p_mw, e_mwh, eta, 0.1, 0.9, 0.0)
            rev_perfect_eval = rev

        penalty = 100 * (pf_rev - rev) / pf_rev if pf_rev else 0.0
        gap_vs_perfect_rh = (
            100 * (rev_perfect_eval - rev) / rev_perfect_eval if rev_perfect_eval else 0.0
        )
        eval_years = max(len(eval_index) * index_dt_hours(eval_index) / 8760.0, 1e-9)
        rows.append({
            "zone": zone,
            "rolling_revenue": rev,
            "pf_revenue": pf_rev,
            "penalty_pct": penalty,
            "eur_per_kw_yr": max(rev, 0.0) / (p_mw * 1000) / eval_years,
            "mode": mode,
            "rh_perfect_revenue": rev_perfect_eval,
            "gap_vs_perfect_rh_pct": gap_vs_perfect_rh,
            "n_scenarios": n_scenarios if mode == "stochastic" else 0,
            "cvar_alpha": cvar_alpha,
            "cvar_weight": cvar_weight,
        })
        soc_store[zone] = soc

    out = pd.DataFrame(rows).sort_values("rolling_revenue", ascending=False).reset_index(drop=True)
    write_valuation_rh(out, paths)  # validates against VALUATION_RH_SCHEMA before writing

    print("\nRealistic vs perfect-foresight gap:")
    print(out[["zone", "rolling_revenue", "rh_perfect_revenue", "gap_vs_perfect_rh_pct", "penalty_pct"]].to_string(index=False))

    plt.figure(figsize=(8, 4))
    x = range(len(out))
    plt.bar([i - 0.2 for i in x], out["pf_revenue"], width=0.4, label="PF (theoretical)")
    plt.bar([i + 0.2 for i in x], out["rolling_revenue"], width=0.4,
            label="Stochastic MPC" if mode == "stochastic" else "RH (perfect)")
    plt.xticks(list(x), out["zone"])
    plt.title("PF vs realistic rolling-horizon revenue")
    plt.legend()
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_pf_vs_rh.png", dpi=150)
    plt.close()

    # Show SOC for the best zone over a representative week.
    zone = out.iloc[0]["zone"]
    soc_best = soc_store[zone]
    week_start = soc_best.index.min()
    week_end = week_start + pd.Timedelta(days=7)
    plt.figure(figsize=(10, 4))
    plt.plot(soc_best.loc[week_start:week_end], label="SOC")
    plt.title(f"RH SOC overlay ({zone}, representative week, mode={mode})")
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_rh_soc_overlay.png", dpi=150)
    plt.close()


if __name__ == "__main__":
    main()
