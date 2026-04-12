#!/usr/bin/env python3
"""Script 8: generate risk alerts and a concise BESS siting memo."""
from __future__ import annotations

import argparse

import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config, write_json


def classify(value: float, yellow: float, red: float, high_bad: bool = True) -> str:
    # Convert a raw metric value to an alert severity label using threshold bands.
    # - For high_bad=True, larger values are worse (upside risk).
    # - For high_bad=False, smaller values are worse (downside risk).
    # Threshold priority: red over yellow; green otherwise.
    if high_bad:
        if value >= red:
            return "red"
        if value >= yellow:
            return "yellow"
        return "green"
    if value <= red:
        return "red"
    if value <= yellow:
        return "yellow"
    return "green"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    th = cfg.get("thresholds", {})

    zs = pd.read_parquet(paths["tables"] / "zone_stats.parquet")
    sp = pd.read_parquet(paths["tables"] / "spread_metrics.parquet")
    rg = pd.read_parquet(paths["tables"] / "regime_profiles.parquet")
    pf = pd.read_parquet(paths["tables"] / "valuation_pf.parquet")
    rh = pd.read_parquet(paths["tables"] / "valuation_rh.parquet")

    alerts = {}

    # Volatility compression: lower IQR is worse when high_bad=False.
    iqr_median = float(zs["iqr"].median())
    alerts["volatility_compression"] = {
        "metric": iqr_median,
        "severity": classify(iqr_median, th.get("iqr_yellow", 25), th.get("iqr_red", 15), high_bad=False),
        "action": "Prefer zones with durable spread opportunities; reduce merchant-only exposure.",
    }

    # Tail dependence risk: max tail ratio with high values being bad.
    tail = float(zs["tail_ratio"].max())
    alerts["tail_dependence"] = {
        "metric": tail,
        "severity": classify(tail, th.get("tail_yellow", 2.5), th.get("tail_red", 3.5), high_bad=True),
        "action": "Stress-test downside under clipped tails and add downside reserves.",
    }

    # Congestion persistence risk: how many consecutive hours are separated by a spread.
    cong = float(sp["max_consecutive_separation_h"].max()) if len(sp) else 0.0
    alerts["congestion_persistence"] = {
        "metric": cong,
        "severity": classify(cong, th.get("congestion_yellow_h", 12), th.get("congestion_red_h", 24), high_bad=True),
        "action": "Prioritize siting near persistent constrained interfaces.",
    }

    # Oversupply risk: maximum of zone negative-price frequency and regime negative-price share.
    over = float(max(zs["negative_price_freq"].max(), rg["neg_price_share"].max()))
    alerts["oversupply_risk"] = {
        "metric": over,
        "severity": classify(over, th.get("oversupply_yellow", 0.05), th.get("oversupply_red", 0.1), high_bad=True),
        "action": "Pair strategy with capture-ready charging and ancillary optionality.",
    }

    # Foresight risk: maximum penalty percent from RH valuation outcomes.
    foresight = float(rh["penalty_pct"].max())
    alerts["foresight_risk"] = {
        "metric": foresight,
        "severity": classify(foresight, th.get("foresight_yellow", 20), th.get("foresight_red", 35), high_bad=True),
        "action": "Invest in short-term forecasting and dispatch tooling before scaling.",
    }

    write_json(paths["alerts"] / "alerts.json", alerts)

    md_lines = ["# Alerts", ""]
    for name, item in alerts.items():
        md_lines.append(f"- **{name}**: {item['severity']} (metric={item['metric']:.3f}) — {item['action']}")
    (paths["alerts"] / "alerts.md").write_text("\n".join(md_lines), encoding="utf-8")

    pf_top = pf.sort_values("eur_per_kw_yr", ascending=False).head(2)
    rh_top = rh.sort_values("eur_per_kw_yr", ascending=False).head(2)

    # Load NTC-constrained network valuation if available (produced by bess_valuation_pf.py).
    # ntc_discount_pct = how much value the network takes away from unconstrained arbitrage.
    # High discount → BESS can only trade when local zone surplus/deficit aligns with
    # price peaks; the theoretical arbitrage value overstates what's capturable.
    ntc_section = []
    comp_path = paths["tables"] / "valuation_pf_comparison.parquet"
    if comp_path.exists():
        comp = pd.read_parquet(comp_path).sort_values("ntc_discount_pct", ascending=True)
        ntc_section = [
            "",
            "## NTC network discount (unconstrained vs network-constrained)",
            "Measures how much arbitrage value the NTC limits remove from each zone.",
            "Low discount = battery can capture most theoretical value locally.",
            "",
            *[
                f"- {r.zone}: {r.eur_per_kw_yr_unc:.1f} → {r.eur_per_kw_yr_net:.1f} €/kW-yr "
                f"({r.ntc_discount_pct:.0f}% discount)"
                for r in comp.itertuples()
            ],
        ]

    memo = [
        "# Baseload Phase 1 MVP Memo",
        "",
        "## Recommended zones (unconstrained PF)",
        *[f"- {r.zone}: {r.eur_per_kw_yr:.2f} €/kW-yr" for r in pf_top.itertuples()],
        "",
        "## Recommended zones (rolling horizon, 24h lookahead)",
        *[f"- {r.zone}: {r.eur_per_kw_yr:.2f} €/kW-yr (foresight penalty {r.penalty_pct:.1f}%)" for r in rh_top.itertuples()],
        *ntc_section,
        "",
        "## Key evidence",
        "- Congestion signal: see `artifacts/figures/spread_heatmap.png`.",
        "- Regime signal: see `artifacts/figures/regime_calendar.png`.",
        "- NTC dispatch: see `artifacts/figures/valuation_pf_ranking.png`.",
        "",
        "## Key risks and actions",
        *[f"- {k}: {v['severity']} — {v['action']}" for k, v in alerts.items()],
        "",
        "## What would change our mind",
        "- Sustained compression in spread/separation metrics for top zones.",
        "- RH penalty remains high after forecast/control improvements.",
        "- Regime mix shifts away from volatility-rich days for multiple months.",
        "- NTC discount narrows significantly (interconnector upgrades or policy change).",
    ]
    (paths["memo"] / "memo.md").write_text("\n".join(memo), encoding="utf-8")


if __name__ == "__main__":
    main()
