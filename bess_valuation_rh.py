#!/usr/bin/env python3
"""Script 7: rolling-horizon BESS valuation and foresight penalty analysis."""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config

try:
    import pulp
except ImportError as exc:  # pragma: no cover
    raise ImportError("PuLP is required for bess valuation scripts. Install with `pip install pulp`.") from exc


def solve_window(price: pd.Series, p_mw: float, e_mwh: float, eta: float, soc0: float):
    t = range(len(price))
    model = pulp.LpProblem("bess_rh", pulp.LpMaximize)
    ch = pulp.LpVariable.dicts("ch", t, lowBound=0, upBound=p_mw)
    dis = pulp.LpVariable.dicts("dis", t, lowBound=0, upBound=p_mw)
    soc = pulp.LpVariable.dicts("soc", t, lowBound=0.1 * e_mwh, upBound=0.9 * e_mwh)
    model += pulp.lpSum([price.iloc[i] * (dis[i] - ch[i]) for i in t])
    for i in t:
        if i == 0:
            model += soc[i] == soc0 + eta * ch[i] - dis[i] / eta
        else:
            model += soc[i] == soc[i - 1] + eta * ch[i] - dis[i] / eta
    model.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[model.status] != "Optimal":
        raise RuntimeError("RH optimization failed")
    return ch[0].value(), dis[0].value(), soc[0].value()


def run_rh(price: pd.Series, p_mw: float, e_mwh: float, eta: float, lookahead: int, step: int):
    soc = 0.5 * e_mwh
    ch, dis, soc_trace = [], [], []
    idx = list(price.index)
    for i in range(0, len(idx), step):
        j = min(i + lookahead, len(idx))
        win = price.iloc[i:j]
        if win.empty:
            break
        c0, d0, soc0 = solve_window(win, p_mw, e_mwh, eta, soc)
        for _ in range(min(step, len(idx) - i)):
            ch.append(c0)
            dis.append(d0)
            soc_trace.append(soc0)
        soc = soc0
    ch_s = pd.Series(ch[: len(price)], index=price.index)
    dis_s = pd.Series(dis[: len(price)], index=price.index)
    soc_s = pd.Series(soc_trace[: len(price)], index=price.index)
    rev = (price * (dis_s - ch_s)).sum()
    return rev, soc_s


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = pd.read_parquet(paths["processed"] / "prices.parquet")
    pf = pd.read_parquet(paths["tables"] / "valuation_pf.parquet").set_index("zone")

    bcfg = cfg.get("bess", {})
    p_mw = float(bcfg.get("p_mw", 50))
    e_mwh = float(bcfg.get("e_mwh", 200))
    eta = float(bcfg.get("eta", 0.9))
    rh_cfg = cfg.get("rolling", {})
    lookahead = int(rh_cfg.get("lookahead_h", 24))
    step = int(rh_cfg.get("step_h", 1))

    rows = []
    soc_store = {}
    for zone in sorted(prices.columns):
        rev, soc = run_rh(prices[zone].fillna(method="ffill").fillna(0), p_mw, e_mwh, eta, lookahead, step)
        pf_rev = float(pf.loc[zone, "net_revenue"])
        penalty = 100 * (pf_rev - rev) / pf_rev if pf_rev else 0.0
        rows.append({"zone": zone, "rolling_revenue": rev, "pf_revenue": pf_rev, "penalty_pct": penalty, "eur_per_kw_yr": rev / (p_mw * 1000)})
        soc_store[zone] = soc

    out = pd.DataFrame(rows).sort_values("rolling_revenue", ascending=False).reset_index(drop=True)
    out.to_parquet(paths["tables"] / "valuation_rh.parquet")

    plt.figure(figsize=(8, 4))
    x = range(len(out))
    plt.bar([i - 0.2 for i in x], out["pf_revenue"], width=0.4, label="PF")
    plt.bar([i + 0.2 for i in x], out["rolling_revenue"], width=0.4, label="RH")
    plt.xticks(list(x), out["zone"])
    plt.title("PF vs RH revenue")
    plt.legend()
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_pf_vs_rh.png", dpi=150)
    plt.close()

    zone = out.iloc[0]["zone"]
    week_start = prices.index.min()
    week_end = week_start + pd.Timedelta(days=7)
    plt.figure(figsize=(10, 4))
    plt.plot(soc_store[zone].loc[week_start:week_end], label="RH SOC")
    plt.title(f"RH SOC overlay ({zone}, representative week)")
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_rh_soc_overlay.png", dpi=150)
    plt.close()


if __name__ == "__main__":
    main()
