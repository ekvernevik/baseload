#!/usr/bin/env python3
"""Script 6: perfect-foresight BESS valuation using a linear program."""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config

try:
    import pulp
except ImportError as exc:  # pragma: no cover
    raise ImportError("PuLP is required for bess valuation scripts. Install with `pip install pulp`.") from exc


def solve_pf(price: pd.Series, p_mw: float, e_mwh: float, eta: float, soc_min: float, soc_max: float, throughput_cost: float):
    t = range(len(price))
    model = pulp.LpProblem("bess_pf", pulp.LpMaximize)
    ch = pulp.LpVariable.dicts("ch", t, lowBound=0, upBound=p_mw)
    dis = pulp.LpVariable.dicts("dis", t, lowBound=0, upBound=p_mw)
    soc = pulp.LpVariable.dicts("soc", t, lowBound=soc_min * e_mwh, upBound=soc_max * e_mwh)

    model += pulp.lpSum(
        [price.iloc[i] * (dis[i] - ch[i]) - throughput_cost * (ch[i] + dis[i]) for i in t]
    )

    for i in t:
        if i == 0:
            model += soc[i] == 0.5 * e_mwh + eta * ch[i] - dis[i] / eta
        else:
            model += soc[i] == soc[i - 1] + eta * ch[i] - dis[i] / eta

    model.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[model.status] != "Optimal":
        raise RuntimeError("PF optimization failed")

    ch_s = pd.Series([ch[i].value() for i in t], index=price.index)
    dis_s = pd.Series([dis[i].value() for i in t], index=price.index)
    soc_s = pd.Series([soc[i].value() for i in t], index=price.index)
    rev = (price * (dis_s - ch_s)).sum() - throughput_cost * (ch_s + dis_s).sum()
    throughput = (ch_s + dis_s).sum()
    cycles = throughput / (2 * e_mwh)
    return rev, throughput, cycles, soc_s, ch_s, dis_s


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = pd.read_parquet(paths["processed"] / "prices.parquet")
    bcfg = cfg.get("bess", {})
    p_mw = float(bcfg.get("p_mw", 50))
    e_mwh = float(bcfg.get("e_mwh", 200))
    eta = float(bcfg.get("eta", 0.9))
    soc_min = float(bcfg.get("soc_min", 0.1))
    soc_max = float(bcfg.get("soc_max", 0.9))
    tcost = float(bcfg.get("throughput_cost", 0.0))

    rows = []
    traces = {}
    for zone in sorted(prices.columns):
        rev, throughput, cycles, soc, ch, dis = solve_pf(prices[zone].fillna(method="ffill").fillna(0), p_mw, e_mwh, eta, soc_min, soc_max, tcost)
        rows.append(
            {
                "zone": zone,
                "gross_revenue": rev,
                "net_revenue": rev,
                "throughput_mwh": throughput,
                "cycles": cycles,
                "eur_per_kw_yr": rev / (p_mw * 1000),
            }
        )
        traces[zone] = {"soc": soc, "ch": ch, "dis": dis}

    valuation = pd.DataFrame(rows).sort_values("net_revenue", ascending=False).reset_index(drop=True)
    valuation.to_parquet(paths["tables"] / "valuation_pf.parquet")

    plt.figure(figsize=(8, 4))
    plt.bar(valuation["zone"], valuation["eur_per_kw_yr"])
    plt.title("PF valuation ranking")
    plt.ylabel("€/kW-yr")
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_pf_ranking.png", dpi=150)
    plt.close()

    top_zone = valuation.iloc[0]["zone"]
    week_start = prices.index.min()
    week_end = week_start + pd.Timedelta(days=7)
    trace = traces[top_zone]
    plt.figure(figsize=(10, 4))
    plt.plot(trace["soc"].loc[week_start:week_end], label="SOC (MWh)")
    plt.plot(trace["ch"].loc[week_start:week_end], label="Charge MW", alpha=0.7)
    plt.plot(trace["dis"].loc[week_start:week_end], label="Discharge MW", alpha=0.7)
    plt.title(f"PF dispatch profile ({top_zone}, representative week)")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_pf_soc_dispatch.png", dpi=150)
    plt.close()


if __name__ == "__main__":
    main()
