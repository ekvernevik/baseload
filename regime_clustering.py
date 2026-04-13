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

    # Daily aggregation: captures structural market states (high-hydro/spill,
    # cold-snap demand, etc.) without over-fitting to hourly peak/off-peak
    # patterns that would appear in any regime regardless of market conditions.
    base = prices.mean(axis=1)
    daily = pd.DataFrame(index=base.resample("D").mean().index)
    daily["daily_mean_price"] = base.resample("D").mean()
    daily["daily_iqr"] = base.resample("D").quantile(0.75) - base.resample("D").quantile(0.25)
    daily["neg_price_share"] = (base < 0).resample("D").mean()

    # Spread features let k-means separate congested from uncongested days even
    # when mean prices are similar across zones. Without them, the model can't
    # distinguish a cheap day with low congestion from a cheap day with high
    # NO1-NO5 separation — which look identical on price level alone.
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
    # Fixed seed ensures regime labels are deterministic across pipeline re-runs,
    # so the memo compares apples to apples if re-generated with the same data.
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

    # Monthly trend shows whether regimes are seasonal — important for sizing
    # BESS revenue forecasts if the deployment period differs from the training year.
    trend = daily.groupby([daily.index.tz_localize(None).to_period("M"), "cluster"]).size().unstack(fill_value=0)
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
