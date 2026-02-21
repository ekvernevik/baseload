#!/usr/bin/env python3
"""Script 4: compute spread and congestion persistence metrics."""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import pandas as pd

from baseload.pipeline_utils import consecutive_true_max, ensure_dirs, load_config


def parse_pair(pair):
    if isinstance(pair, str) and "-" in pair:
        a, b = pair.split("-", 1)
        return a.strip(), b.strip()
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        return pair[0], pair[1]
    raise ValueError(f"Invalid pair format: {pair}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    prices = pd.read_parquet(paths["processed"] / "prices.parquet")

    sep_threshold = cfg.get("thresholds", {}).get("spread_abs", 20.0)
    pairs = cfg.get("pairs", [])
    if not pairs:
        cols = sorted(prices.columns)
        pairs = [f"{cols[0]}-{cols[1]}"] if len(cols) >= 2 else []

    spread_map, metrics = {}, []
    for pair in pairs:
        a, b = parse_pair(pair)
        if a not in prices.columns or b not in prices.columns:
            continue
        d = prices[a] - prices[b]
        key = f"{a}-{b}"
        spread_map[key] = d
        sep = d.abs() > sep_threshold
        metrics.append(
            {
                "pair": key,
                "mean_spread": d.mean(),
                "std_spread": d.std(ddof=0),
                "p95_abs_spread": d.abs().quantile(0.95),
                "separation_freq": sep.mean(),
                "max_consecutive_separation_h": consecutive_true_max(sep),
            }
        )

    spread_metrics = pd.DataFrame(metrics).sort_values("pair").reset_index(drop=True)
    spread_metrics.to_parquet(paths["tables"] / "spread_metrics.parquet")

    if spread_map:
        spread_df = pd.DataFrame(spread_map)
        top_cols = spread_metrics.sort_values("p95_abs_spread", ascending=False)["pair"].head(5)
        plt.figure(figsize=(12, 4))
        plt.imshow(spread_df[top_cols].T, aspect="auto", interpolation="nearest", cmap="coolwarm")
        plt.yticks(range(len(top_cols)), top_cols)
        plt.colorbar(label="€/MWh")
        plt.title("Spread heatmap (top pairs)")
        plt.tight_layout()
        plt.savefig(paths["figures"] / "spread_heatmap.png", dpi=150)
        plt.close()


if __name__ == "__main__":
    main()
