#!/usr/bin/env python3
"""5-zone PyPSA transport model for Norway (NO1–NO5).

Builds a NTC-constrained network with fixed observed net injections
(actgen + external_balance - load) per zone. The LP routes power through
NTC-limited links without re-optimizing generation dispatch.

Primary use: `build_network()` is called by bess_valuation_pf.py to provide
the network context for NTC-constrained BESS dispatch optimization.

Note: running this file as a script also produces shadow price outputs
(pypsa_shadow_prices.parquet, pypsa_lmp.parquet), but these are not useful
for Norway — shadow prices require real generator marginal costs, which are
not observable for hydro (~97% of Norwegian generation). Those outputs are
kept for reference but are not used downstream.
"""
from __future__ import annotations

import argparse
import warnings

import pandas as pd

# Suppress pandas ArrowStringArray incompatibility with PyPSA 1.x
import pandas as _pd
_pd.options.future.infer_string = False

import pypsa  # noqa: E402 (must come after pandas option set)

from baseload.pipeline_utils import ensure_dirs, index_dt_hours, load_config
from baseload.zones import DEFAULT_NTC_MW as NTC_MW, DEFAULT_ZONES as ZONES, ntc_from_cfg, zones_from_cfg

# Slack generator cost — high enough to dominate; in €/MWh.
# Any LMP exceeding this signals infeasibility / severe data gap.
SLACK_COST = 10_000.0


def _strip_tz(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Return timezone-naive index; PyPSA 1.x does not support tz-aware."""
    if idx.tz is not None:
        return idx.tz_localize(None)
    return idx


def build_network(
    actgen: pd.DataFrame,
    load: pd.DataFrame,
    external_balance: pd.DataFrame | None = None,
    zones: list[str] | None = None,
    ntc: dict[str, float] | None = None,
) -> tuple[pypsa.Network, pd.DatetimeIndex]:
    """Construct the PyPSA fixed-injection transport network.

    Fixed-injection formulation: observed net position per zone (actgen +
    external_balance - load) is fixed. The LP ONLY routes power through
    NTC-limited links. LMP duals on binding link constraints are the shadow
    prices. A tiny slack (cost = SLACK_COST) covers the ~14 MW system
    rounding imbalance and rare hours where NTC limits are collectively
    infeasible.

    Parameters
    ----------
    actgen:
        Hourly generation per zone summed across types (MW), shape (T, 5).
    load:
        Hourly load per zone (MW), shape (T, 5).
    external_balance:
        Net external import per zone (MW), positive = import.

    Returns
    -------
    (network, common_idx) — network ready for n.optimize().
    """
    if zones is None:
        zones = ZONES
    if ntc is None:
        ntc = NTC_MW

    # Align all inputs on common index
    idx_list = [actgen.index, load.index]
    if external_balance is not None:
        idx_list.append(external_balance.index)
    common_idx = idx_list[0]
    for idx in idx_list[1:]:
        common_idx = common_idx.intersection(idx)

    actgen = actgen.loc[common_idx]
    load = load.loc[common_idx]

    # Observed net injection per zone = actgen + external_import - load
    # positive → surplus (exports to other NO zones)
    # negative → deficit (imports from other NO zones)
    if external_balance is not None:
        ext = external_balance.loc[common_idx].reindex(columns=zones, fill_value=0.0)
    else:
        ext = pd.DataFrame(0.0, index=common_idx, columns=zones)

    net_inj = (actgen + ext - load).fillna(0.0)

    snapshots = _strip_tz(common_idx)

    n = pypsa.Network()
    n.set_snapshots(snapshots)

    # Sub-hourly data: weight each snapshot by its length in hours so energy
    # balances and objective terms stay in MWh/EUR at any resolution.
    dt = index_dt_hours(common_idx)
    if abs(dt - 1.0) > 1e-9:
        n.snapshot_weightings.loc[:, :] = dt

    # --- Buses (one per bidding zone) ---
    for zone in zones:
        n.add("Bus", zone, carrier="AC")

    # --- Bidirectional NTC links (transport model — no KVL) ---
    # Tiny marginal cost (1e-4 €/MWh) makes the LP prefer minimum-flow
    # solutions when multiple feasible routings exist; does not materially
    # affect shadow prices.
    for pair, ntc_mw in ntc.items():
        zone_a, zone_b = pair.split("-")
        n.add(
            "Link",
            pair,
            carrier="AC",
            bus0=zone_a,
            bus1=zone_b,
            p_nom=ntc_mw,
            p_min_pu=-1.0,
            p_max_pu=1.0,
            marginal_cost=1e-4,
        )

    # --- Fixed injections per zone ---
    # Surplus zones: fixed generator (p_min = p_max = surplus[t])
    # Deficit zones: fixed load (p_set = |deficit[t]|)
    # Both cannot vary — the LP only controls link flows.
    for zone in zones:
        ni = net_inj[zone]
        surplus = ni.clip(lower=0.0)
        deficit = (-ni).clip(lower=0.0)

        p_nom_sur = float(surplus.max()) if surplus.max() > 0 else 1.0
        p_pu = (surplus / p_nom_sur).clip(0.0, 1.0)

        n.add(
            "Generator",
            f"{zone}_inj",
            carrier="AC",
            bus=zone,
            p_nom=p_nom_sur,
            marginal_cost=0.0,
        )
        n.generators_t.p_max_pu[f"{zone}_inj"] = p_pu.values
        # p_min = 0: curtailable (prevents infeasibility when surplus can't be routed)

        n.add(
            "Load",
            f"{zone}_dem",
            carrier="AC",
            bus=zone,
            p_set=deficit.values,
        )

        # Small slack for numerical infeasibility (NTC collectively binding)
        n.add(
            "Generator",
            f"{zone}_slack",
            carrier="AC",
            bus=zone,
            p_nom=5_000.0,
            p_min_pu=0.0,
            p_max_pu=1.0,
            marginal_cost=SLACK_COST,
        )

    return n, common_idx


def run_opf(n: pypsa.Network, ntc: dict[str, float] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run LP and return (shadow_prices, lmps).

    shadow_prices: DataFrame (T, n_pairs) — LMP_a − LMP_b per zone pair.
    lmps: DataFrame (T, n_zones) — nodal LMPs per zone (in units of SLACK_COST).
    """
    if ntc is None:
        ntc = NTC_MW
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        status, condition = n.optimize(
            solver_name="highs",
            include_objective_constant=False,
        )

    if status not in ("ok", "warning"):
        raise RuntimeError(f"OPF failed: status={status}, condition={condition}")

    lmps_raw = n.buses_t.marginal_price.copy()
    lmps_raw.index = n.snapshots

    shadow_prices = pd.DataFrame(index=lmps_raw.index, columns=list(ntc), dtype=float)
    for pair in ntc:
        zone_a, zone_b = pair.split("-")
        shadow_prices[pair] = lmps_raw[zone_a] - lmps_raw[zone_b]

    return shadow_prices, lmps_raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    zones = zones_from_cfg(cfg)
    ntc = ntc_from_cfg(cfg)

    print("Loading inputs...")
    prices = pd.read_parquet(paths["processed"] / "prices.parquet")
    load = pd.read_parquet(paths["processed"] / "load.parquet")
    actgen_raw = pd.read_parquet(paths["processed"] / "actgen.parquet")

    # Sum actgen across generation types → zone totals
    if isinstance(actgen_raw.columns, pd.MultiIndex):
        actgen = actgen_raw.T.groupby(level=0).sum().T
    else:
        actgen = actgen_raw.copy()
    actgen = actgen[zones]

    print(f"  Snapshots: {len(prices)}")
    print(f"  Zones: {zones}")
    print(f"  Pairs: {list(ntc)}")

    ext_bal_path = paths["processed"] / "external_balance.parquet"
    external_balance = pd.read_parquet(ext_bal_path) if ext_bal_path.exists() else None
    if external_balance is not None:
        print(f"  Including external balance ({ext_bal_path.name})")

    print("\nBuilding PyPSA network (fixed-injection formulation)...")
    n, common_idx = build_network(actgen, load, external_balance=external_balance, zones=zones, ntc=ntc)

    print("Running LP (HiGHS)...")
    shadow_prices, lmps = run_opf(n, ntc=ntc)

    # Restore original tz-aware index for consistency with pipeline
    shadow_prices.index = common_idx
    lmps.index = common_idx

    # Save outputs
    sp_path = paths["tables"] / "pypsa_shadow_prices.parquet"
    lmp_path = paths["tables"] / "pypsa_lmp.parquet"
    shadow_prices.to_parquet(sp_path)
    lmps.to_parquet(lmp_path)
    print(f"\nSaved shadow prices -> {sp_path}")
    print(f"Saved LMPs         -> {lmp_path}")

    # Summary — shadow prices here are in dual units (0 = uncongested, nonzero = congested)
    print("\nCongestion stats per pair (shadow price != 0 → NTC binding):")
    for pair in ntc:
        sp = shadow_prices[pair].dropna()
        congested = (sp.abs() > 1e-3).sum()
        print(
            f"  {pair}: congested {congested} hrs ({100*congested/len(sp):.1f}%)"
            f"  mean_sp={sp[sp.abs()>1e-3].mean() if congested else 0:+.1f}"
        )

    # Validate against observed prices: correlation of shadow price with observed spread
    print("\nValidation — correlation of PyPSA shadow price with observed price spread:")
    prices_aligned = prices.reindex(common_idx).ffill().bfill()
    for pair in ntc:
        zone_a, zone_b = pair.split("-")
        obs_spread = prices_aligned[zone_a] - prices_aligned[zone_b]
        sp = shadow_prices[pair].fillna(0.0)
        congested_mask = sp.abs() > 1e-3
        if congested_mask.sum() > 10:
            corr = obs_spread[congested_mask].corr(sp[congested_mask])
            print(f"  {pair}: r={corr:.3f} (congested hours only)")

    # Check for slack usage (data quality indicator)
    slack_gens = [f"{z}_slack" for z in zones]
    available_slack = [g for g in slack_gens if g in n.generators_t.p.columns]
    if available_slack:
        slack_total = n.generators_t.p[available_slack].sum(axis=1)
        slack_hours = (slack_total > 1.0).sum()
        if slack_hours > 0:
            print(
                f"\nWARNING: Slack active in {slack_hours} hrs "
                f"(avg {slack_total[slack_total > 1].mean():.0f} MW) — "
                "NTC limits collectively infeasible in these hours."
            )


if __name__ == "__main__":
    main()
