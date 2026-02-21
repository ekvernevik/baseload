#!/usr/bin/env python3
"""Script 5: cluster days into market regimes using deterministic k-means."""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import pandas as pd
from sklearn.cluster import KMeans

from baseload.pipeline_utils import ensure_dirs, load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = pd.read_parquet(paths["processed"] / "prices.parquet")
    spread_metrics = paths["tables"] / "spread_metrics.parquet"

    base = prices.mean(axis=1)
    daily = pd.DataFrame(index=base.resample("D").mean().index)
    daily["daily_mean_price"] = base.resample("D").mean()
    daily["daily_iqr"] = base.resample("D").quantile(0.75) - base.resample("D").quantile(0.25)
    daily["neg_price_share"] = (base < 0).resample("D").mean()

    if spread_metrics.exists() and prices.shape[1] >= 2:
        a, b = sorted(prices.columns)[:2]
        spread = (prices[a] - prices[b]).abs()
        daily["p95_spread"] = spread.resample("D").quantile(0.95)
        daily["persistence_proxy"] = (spread > spread.quantile(0.8)).resample("D").mean()
    else:
        daily["p95_spread"] = 0.0
        daily["persistence_proxy"] = 0.0

    daily = daily.fillna(0.0)
    k = int(cfg.get("regime", {}).get("k", 3))
    seed = int(cfg.get("regime", {}).get("seed", 42))
    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    daily["cluster"] = km.fit_predict(daily.values)

    profiles = daily.groupby("cluster").mean(numeric_only=True).reset_index().sort_values("cluster")
    profiles.to_parquet(paths["tables"] / "regime_profiles.parquet")

    plt.figure(figsize=(12, 2.8))
    plt.scatter(daily.index, daily["cluster"], c=daily["cluster"], cmap="tab10", s=10)
    plt.title("Regime calendar")
    plt.yticks(range(k))
    plt.tight_layout()
    plt.savefig(paths["figures"] / "regime_calendar.png", dpi=150)
    plt.close()

    trend = daily.groupby([daily.index.to_period("M"), "cluster"]).size().unstack(fill_value=0)
    trend.index = trend.index.astype(str)
    trend.plot(kind="bar", stacked=True, figsize=(10, 4), colormap="tab10")
    plt.title("Regime frequency trend")
    plt.xlabel("Month")
    plt.ylabel("Days")
    plt.tight_layout()
    plt.savefig(paths["figures"] / "regime_frequency_trend.png", dpi=150)
    plt.close()


if __name__ == "__main__":
    main()
