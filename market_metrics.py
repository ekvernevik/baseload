#!/usr/bin/env python3
"""Script 3: compute market statistics and basic price visualizations."""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config, safe_div
from baseload.io import read_prices, write_parquet
from baseload.zones import zones_from_cfg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = read_prices(paths, zones=zones_from_cfg(cfg))  # validates against a schema built for the configured zones
    

    records = []
    for zone in sorted(prices.columns):
        s = prices[zone].dropna()
        p50 = s.quantile(0.5)
        rec = {
            "zone": zone,
            "mean": s.mean(),
            "std": s.std(ddof=0),
            # IQR over std: robust to the extreme spike hours (hydro scarcity,
            # interconnector trips) that dominate std. IQR captures the typical
            # spread opportunity range that a BESS can actually trade.
            "iqr": s.quantile(0.75) - s.quantile(0.25),
            "p5": s.quantile(0.05),
            "p50": p50,
            "p95": s.quantile(0.95),
            "p99": s.quantile(0.99),
            # Negative-price hours are prime charging windows; high frequency
            # signals structural renewable oversupply, not just transient noise.
            "negative_price_freq": (s < 0).mean(),
            # tail_ratio = p99/p50: measures fat-tail behavior. High ratio means
            # arbitrage value is concentrated in rare spike hours — increasing
            # risk if those spikes don't recur.
            "tail_ratio": safe_div(s.quantile(0.99), p50),
        }
        records.append(rec)

    # zone_stats feeds the volatility_compression and tail_dependence alerts downstream.
    stats = pd.DataFrame(records).sort_values("zone").reset_index(drop=True)
    write_parquet(stats, paths["tables"] / "zone_stats.parquet")

    plt.figure(figsize=(12, 4))
    plt.imshow(prices.T, aspect="auto", interpolation="nearest")
    plt.yticks(range(len(prices.columns)), prices.columns)
    plt.colorbar(label="€/MWh")
    plt.title("Price heatmap")
    plt.tight_layout()
    plt.savefig(paths["figures"] / "price_heatmap.png", dpi=150)
    plt.close()

    # Duration curves show tail shape and skew per zone — useful for spotting
    # whether high spreads are driven by a handful of extreme hours or are broad.
    plt.figure(figsize=(8, 5))
    for zone in sorted(prices.columns):
        vals = prices[zone].dropna().sort_values(ascending=False).reset_index(drop=True)
        plt.plot(vals.values, label=zone)
    plt.title("Price duration curves")
    plt.xlabel("Hour rank")
    plt.ylabel("€/MWh")
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(paths["figures"] / "price_duration_curves.png", dpi=150)
    plt.close()


if __name__ == "__main__":
    main()
