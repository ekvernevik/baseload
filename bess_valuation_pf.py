#!/usr/bin/env python3
"""Script 6: perfect-foresight BESS valuation using a linear program.

Two formulations:
  solve_pf          — zone-level unconstrained LP (PuLP/CBC)
  solve_pf_network  — NTC-constrained LP via PyPSA; co-optimizes BESS dispatch
                      alongside inter-zone flows at the siting zone
"""
from __future__ import annotations

import argparse
import warnings

import matplotlib.pyplot as plt
import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config

from baseload.io import read_prices, write_valuation_pf, read_load, read_gen, read_external_balance, write_parquet

try:
    import pulp
except ImportError as exc:  # pragma: no cover
    raise ImportError("PuLP is required for bess valuation scripts. Install with `pip install pulp`.") from exc

# Suppress pandas ArrowStringArray incompatibility with PyPSA 1.x
import pandas as _pd
_pd.options.future.infer_string = False

import pypsa  # noqa: E402 (must come after pandas option set)

from norway_network import build_network, _strip_tz  # noqa: E402


def solve_pf(price: pd.Series, p_mw: float, e_mwh: float, eta: float, soc_min: float, soc_max: float, throughput_cost: float):
    # Build perfect-foresight LP for 24/7 charging/discharging dispatch.
    # t indexes time steps for the historic price series.
    t = range(len(price))

    model = pulp.LpProblem("bess_pf", pulp.LpMaximize)

    # Define decision variables (MW) with power limits.
    ch = pulp.LpVariable.dicts("ch", t, lowBound=0, upBound=p_mw)
    dis = pulp.LpVariable.dicts("dis", t, lowBound=0, upBound=p_mw)

    # Define state-of-charge (MWh) with energy limits.
    soc = pulp.LpVariable.dicts("soc", t, lowBound=soc_min * e_mwh, upBound=soc_max * e_mwh)

    # Objective: maximize revenue minus throughput operating cost.
    model += pulp.lpSum(
        [price.iloc[i] * (dis[i] - ch[i]) - throughput_cost * (ch[i] + dis[i]) for i in t]
    )

    # Energy balance constraints across the horizon with efficiency losses.
    for i in t:
        if i == 0:
            model += soc[i] == 0.5 * e_mwh + eta * ch[i] - dis[i] / eta
        else:
            model += soc[i] == soc[i - 1] + eta * ch[i] - dis[i] / eta

    # Solve with CBC; require an optimal solution.
    model.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[model.status] != "Optimal":
        raise RuntimeError("PF optimization failed")

    # Collect time series outputs and high-level summary metrics.
    ch_s = pd.Series([ch[i].value() for i in t], index=price.index)
    dis_s = pd.Series([dis[i].value() for i in t], index=price.index)
    soc_s = pd.Series([soc[i].value() for i in t], index=price.index)
    rev = (price * (dis_s - ch_s)).sum() - throughput_cost * (ch_s + dis_s).sum()
    throughput = (ch_s + dis_s).sum()
    cycles = throughput / (2 * e_mwh)

    return rev, throughput, cycles, soc_s, ch_s, dis_s


def solve_pf_network(
    zone: str,
    prices: pd.DataFrame,
    actgen: pd.DataFrame,
    load: pd.DataFrame,
    external_balance: pd.DataFrame | None,
    p_mw: float,
    e_mwh: float,
    eta: float,
    soc_min: float,
    soc_max: float,
    throughput_cost: float,
) -> tuple[float, float, float, pd.Series, pd.Series, pd.Series]:
    """Network-constrained perfect-foresight BESS dispatch via PyPSA.

    Sites a StorageUnit at *zone* and co-optimizes charge/discharge alongside
    NTC-limited inter-zone flows. Revenue signal = observed zone prices.

    The charging cost is injected directly into the linopy objective
    (price[t] × p_store[t]) because PyPSA's marginal_cost_storage is a
    standing cost on SOC, not a per-MWh charging cost.

    An internal degeneracy-breaking epsilon (1e-3 €/MWh) is added to both
    dispatch cost and charge cost. This ensures the LP prefers profitable
    arbitrage over the degenerate simultaneous-charge/discharge solution that
    has the same zero P&L but burns battery cycles.

    Returns the same tuple as solve_pf: (rev, throughput, cycles, soc_s, ch_s, dis_s).
    """
    import xarray as xr

    # Small throughput penalty to prevent simultaneous charge/discharge degeneracy.
    # Does not materially affect revenue (<<1% of typical price spreads).
    _EPS = 1e-3

    n, common_idx = build_network(actgen, load, external_balance=external_balance)

    price_zone = prices[zone].reindex(common_idx).ffill().fillna(0.0)

    n.add(
        "StorageUnit",
        "BESS",
        bus=zone,
        p_nom=p_mw,
        max_hours=e_mwh / p_mw,
        efficiency_store=eta,
        efficiency_dispatch=eta,
        cyclic_state_of_charge=False,
        state_of_charge_initial=0.5 * e_mwh,
        marginal_cost=0.0,  # overridden by time-varying below
    )

    # Dispatch revenue: negative marginal cost = revenue for the minimizing LP.
    # throughput_cost + epsilon break degeneracy and represent cycle cost.
    n.storage_units_t.marginal_cost = pd.DataFrame(
        {"BESS": (-price_zone + throughput_cost + _EPS).values},
        index=n.snapshots,
    )

    # Build the linopy model so we can inject charging cost directly.
    # (marginal_cost_storage is a SOC standing cost, not a p_store cost.)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = n.optimize.create_model()

    p_store_var = m.variables["StorageUnit-p_store"].sel(name="BESS")
    charge_cost_da = xr.DataArray(
        (price_zone + throughput_cost + _EPS).values,
        coords={"snapshot": n.snapshots},
        dims="snapshot",
    )
    m.objective = m.objective + (p_store_var * charge_cost_da).sum()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = n.optimize.solve_model(solver_name="highs")

    status = result[0] if isinstance(result, tuple) else getattr(n, "status", "unknown")
    if status not in ("ok", "warning"):
        raise RuntimeError(f"Network PF OPF failed for {zone}: status={status}")

    su_t = n.storage_units_t

    # Extract dispatch/store time series.
    if "BESS" in getattr(su_t, "p_dispatch", pd.DataFrame()).columns:
        dis_raw = su_t.p_dispatch["BESS"].values
        ch_raw = su_t.p_store["BESS"].values
    else:
        p_net = su_t.p["BESS"].values
        dis_raw = p_net.clip(min=0)
        ch_raw = (-p_net).clip(min=0)

    soc_raw = su_t.state_of_charge["BESS"].values

    dis_s = pd.Series(dis_raw, index=common_idx)
    ch_s = pd.Series(ch_raw, index=common_idx)
    soc_s = pd.Series(soc_raw, index=common_idx)

    rev = (price_zone * (dis_s - ch_s)).sum() - throughput_cost * (dis_s + ch_s).sum()
    throughput = (dis_s + ch_s).sum()
    cycles = throughput / (2 * e_mwh)

    return rev, throughput, cycles, soc_s, ch_s, dis_s


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    # Set up paths, read data, and config BESS parameters from config file.
    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = read_prices(paths)  # validates against PRICES_SCHEMA on load
    bcfg = cfg.get("bess", {})
    p_mw = float(bcfg.get("p_mw", 50))
    e_mwh = float(bcfg.get("e_mwh", 200))
    eta = float(bcfg.get("eta", 0.9))
    soc_min = float(bcfg.get("soc_min", 0.1))
    soc_max = float(bcfg.get("soc_max", 0.9))
    tcost = float(bcfg.get("throughput_cost", 0.0))

    # Load network inputs for constrained model
    load_df = read_load(paths)
    actgen_raw = read_gen(paths)
    if isinstance(actgen_raw.columns, pd.MultiIndex):
        from norway_network import ZONES
        actgen = actgen_raw.T.groupby(level=0).sum().T[ZONES]
    else:
        from norway_network import ZONES
        actgen = actgen_raw[ZONES]

    try:
        external_balance = read_external_balance(paths)
    except FileNotFoundError:
        external_balance = None

    # -----------------------------------------------------------------------
    # Run both formulations per zone
    # -----------------------------------------------------------------------
    rows_unc = []   # unconstrained (PuLP)
    rows_net = []   # network-constrained (PyPSA)
    traces = {}

    for zone in sorted(prices.columns):
        print(f"  {zone} unconstrained...", end=" ", flush=True)
        rev_u, thr_u, cyc_u, soc_u, ch_u, dis_u = solve_pf(
            prices[zone].ffill().fillna(0), p_mw, e_mwh, eta, soc_min, soc_max, tcost
        )
        rows_unc.append({
            "zone": zone,
            "gross_revenue": rev_u,
            "net_revenue": rev_u,
            "throughput_mwh": thr_u,
            "cycles": cyc_u,
            "eur_per_kw_yr": rev_u / (p_mw * 1000),
        })
        print("done")

        print(f"  {zone} network-constrained...", end=" ", flush=True)
        rev_n, thr_n, cyc_n, soc_n, ch_n, dis_n = solve_pf_network(
            zone, prices, actgen, load_df, external_balance,
            p_mw, e_mwh, eta, soc_min, soc_max, tcost,
        )
        rows_net.append({
            "zone": zone,
            "gross_revenue": rev_n,
            "net_revenue": rev_n,
            "throughput_mwh": thr_n,
            "cycles": cyc_n,
            "eur_per_kw_yr": rev_n / (p_mw * 1000),
        })
        print("done")

        traces[zone] = {
            "unc": {"soc": soc_u, "ch": ch_u, "dis": dis_u},
            "net": {"soc": soc_n, "ch": ch_n, "dis": dis_n},
        }

    val_unc = pd.DataFrame(rows_unc).sort_values("net_revenue", ascending=False).reset_index(drop=True)
    val_net = pd.DataFrame(rows_net).sort_values("net_revenue", ascending=False).reset_index(drop=True)

    # Backwards-compatible output: valuation_pf.parquet stays (unconstrained)
    write_valuation_pf(val_unc, paths)  # validates against VALUATION_PF_SCHEMA before writing
    write_parquet(val_net, paths["tables"] / "valuation_pf_network.parquet")

    # Comparison table: one row per zone, both valuations side by side
    comp = val_unc[["zone", "eur_per_kw_yr"]].rename(columns={"eur_per_kw_yr": "eur_per_kw_yr_unc"})
    comp = comp.merge(
        val_net[["zone", "eur_per_kw_yr"]].rename(columns={"eur_per_kw_yr": "eur_per_kw_yr_net"}),
        on="zone",
    )
    comp["ntc_discount_pct"] = 100 * (1 - comp["eur_per_kw_yr_net"] / comp["eur_per_kw_yr_unc"].replace(0, float("nan")))
    write_parquet(comp, paths["tables"] / "valuation_pf_comparison.parquet")
    print("\nValuation comparison (€/kW-yr):")
    print(comp.to_string(index=False))

    # -----------------------------------------------------------------------
    # Plots
    # -----------------------------------------------------------------------

    # Side-by-side zone ranking
    zones_sorted = val_unc["zone"].tolist()
    x = range(len(zones_sorted))
    net_vals = [val_net.set_index("zone").loc[z, "eur_per_kw_yr"] for z in zones_sorted]
    unc_vals = val_unc["eur_per_kw_yr"].tolist()

    fig, ax = plt.subplots(figsize=(9, 4))
    width = 0.35
    ax.bar([i - width / 2 for i in x], unc_vals, width, label="Unconstrained")
    ax.bar([i + width / 2 for i in x], net_vals, width, label="Network-constrained")
    ax.set_xticks(list(x))
    ax.set_xticklabels(zones_sorted)
    ax.set_title("PF valuation: unconstrained vs NTC-constrained")
    ax.set_ylabel("€/kW-yr")
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_pf_ranking.png", dpi=150)
    plt.close()

    # Representative one-week dispatch trace for top network-constrained zone
    top_zone = val_net.iloc[0]["zone"]
    week_start = prices.index.min()
    week_end = week_start + pd.Timedelta(days=7)

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for ax, key, label in zip(axes, ["unc", "net"], ["Unconstrained", "NTC-constrained"]):
        tr = traces[top_zone][key]
        ax.plot(tr["soc"].loc[week_start:week_end], label="SOC (MWh)")
        ax.plot(tr["ch"].loc[week_start:week_end], label="Charge MW", alpha=0.7)
        ax.plot(tr["dis"].loc[week_start:week_end], label="Discharge MW", alpha=0.7)
        ax.set_title(f"{label} dispatch — {top_zone}")
        ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(paths["figures"] / "valuation_pf_soc_dispatch.png", dpi=150)
    plt.close()


if __name__ == "__main__":
    main()
