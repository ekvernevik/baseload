#!/usr/bin/env python3
"""Script 6b: network-aware BESS valuation using PyPSA.

This keeps the single-node PuLP valuation as a baseline and adds an
optional PyPSA-based run that accounts for inter-zonal transfers and
zone-level price signals. It intentionally mirrors the CLI contract of
other scripts: ``--config`` is the only argument and outputs are written
under ``artifacts/``.
"""
from __future__ import annotations

import argparse
from typing import Iterable

import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config

try:  # pragma: no cover
    import pypsa
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyPSA is required for network-aware valuation. Install with `pip install pypsa`." ) from exc


def _default_load(index: pd.Index, zones: Iterable[str]) -> pd.DataFrame:
    """Return a simple 1 MW flat load per zone when no load data exists."""
    base = pd.DataFrame(1.0, index=index, columns=list(zones))
    base.index.name = "time"
    return base


def build_network(prices: pd.DataFrame, load: pd.DataFrame | None, cfg: dict, candidate_zone: str) -> pypsa.Network:
    pypsa_cfg = cfg.get("pypsa", {})
    eta = float(cfg.get("bess", {}).get("eta", 0.9))
    p_mw = float(cfg.get("bess", {}).get("p_mw", 50))
    e_mwh = float(cfg.get("bess", {}).get("e_mwh", 200))
    soc_min = float(cfg.get("bess", {}).get("soc_min", 0.1))
    soc_max = float(cfg.get("bess", {}).get("soc_max", 0.9))
    throughput_cost = float(cfg.get("bess", {}).get("throughput_cost", 0.0))

    n = pypsa.Network()
    n.set_snapshots(prices.index)

    buses = list(prices.columns)
    for zone in buses:
        n.add("Bus", zone)

    # Price-taking generators to reflect zone-specific prices
    for zone in buses:
        n.add(
            "Generator",
            f"{zone}_gen",
            bus=zone,
            p_nom=1e6,
            marginal_cost=prices[zone],
        )

    # Flat load fallback if no load.parquet is provided
    load_profile = load if load is not None else _default_load(prices.index, buses)
    for zone in buses:
        n.add("Load", f"{zone}_load", bus=zone, p_set=load_profile[zone])

    # Inter-zonal transfer capacities; fall back to symmetric default if missing
    link_cap = float(pypsa_cfg.get("link_capacity_mw", 500))
    link_eta = float(pypsa_cfg.get("link_efficiency", 0.95))
    for pair in cfg.get("pairs", []):
        if isinstance(pair, str) and "-" in pair:
            a, b = pair.split("-", 1)
        else:
            continue
        if a not in buses or b not in buses:
            continue
        n.add(
            "Link",
            f"{a}_{b}",
            bus0=a,
            bus1=b,
            p_nom=link_cap,
            efficiency=link_eta,
            marginal_cost=0.0,
        )
        n.add(
            "Link",
            f"{b}_{a}",
            bus0=b,
            bus1=a,
            p_nom=link_cap,
            efficiency=link_eta,
            marginal_cost=0.0,
        )

    # Storage unit representing the candidate BESS
    n.add(
        "StorageUnit",
        f"bess_{candidate_zone}",
        bus=candidate_zone,
        p_nom=p_mw,
        max_hours=e_mwh / p_mw,
        min_state_of_charge=soc_min * e_mwh,
        max_state_of_charge=soc_max * e_mwh,
        efficiency_store=eta,
        efficiency_dispatch=eta,
        standing_loss=0.0,
        marginal_cost=throughput_cost,
        state_of_charge_initial=0.5 * e_mwh,
        cyclic_state_of_charge=True,
    )
    return n


def optimize_network(n: pypsa.Network, solver: str) -> None:
    if hasattr(n, "optimize"):
        n.optimize(solver_name=solver)
    else:  # pragma: no cover - legacy PyPSA
        n.lopf(solver_name=solver, pyomo=False)


def compute_value(prices: pd.DataFrame, n: pypsa.Network, zone: str) -> tuple[float, float, float]:
    # Revenue-style metric from dispatch traces
    su = n.storage_units_t
    dis = su.p_dispatch[f"bess_{zone}"]
    ch = su.p_store[f"bess_{zone}"]
    throughput_cost = float(n.storage_units.at[f"bess_{zone}", "marginal_cost"])
    rev = (prices[zone].reindex(dis.index) * (dis - ch)).sum() - throughput_cost * (ch + dis).sum()

    # Cost-savings metric relative to baseline (generator cost equals price * load)
    load_cols = [c for c in n.loads.index if c.endswith("_load")]
    baseline_cost = 0.0
    for load_name in load_cols:
        bus = n.loads.at[load_name, "bus"]
        p_set = n.loads_t.p_set[load_name]
        baseline_cost += (prices[bus].reindex(p_set.index) * p_set).sum()
    obj = float(getattr(n, "objective", float("nan")))
    savings = baseline_cost - obj if obj == obj else float("nan")
    return rev, savings, (dis + ch).sum()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = pd.read_parquet(paths["processed"] / "prices.parquet")
    load_path = paths["processed"] / "load.parquet"
    load = pd.read_parquet(load_path) if load_path.exists() else None

    solver = cfg.get("pypsa", {}).get("solver", "highs")

    rows = []
    traces = {}
    for zone in sorted(prices.columns):
        net = build_network(prices, load, cfg, candidate_zone=zone)
        optimize_network(net, solver)
        rev, savings, throughput = compute_value(prices, net, zone)
        eur_per_kw_yr = rev / (float(cfg.get("bess", {}).get("p_mw", 50)) * 1000)
        rows.append(
            {
                "zone": zone,
                "net_revenue": rev,
                "cost_savings": savings,
                "throughput_mwh": throughput,
                "eur_per_kw_yr": eur_per_kw_yr,
            }
        )
        traces[zone] = {
            "dis": net.storage_units_t.p_dispatch[f"bess_{zone}"],
            "ch": net.storage_units_t.p_store[f"bess_{zone}"],
            "soc": net.storage_units_t.state_of_charge[f"bess_{zone}"],
        }

    valuation = pd.DataFrame(rows).sort_values("net_revenue", ascending=False).reset_index(drop=True)
    valuation.to_parquet(paths["tables"] / "valuation_pypsa.parquet")

    # Lightweight figure for ranking
    try:  # pragma: no cover - plotting is optional in headless environments
        import matplotlib.pyplot as plt

        plt.figure(figsize=(8, 4))
        plt.bar(valuation["zone"], valuation["eur_per_kw_yr"])
        plt.title("PyPSA valuation ranking")
        plt.ylabel("€/kW-yr")
        plt.tight_layout()
        plt.savefig(paths["figures"] / "valuation_pypsa_ranking.png", dpi=150)
        plt.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
