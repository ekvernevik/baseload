"""Reusable plotting helpers for Baseload pipeline artifacts.

All functions accept a pre-loaded DataFrame/Series so they stay
decoupled from file I/O.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def save_price_heatmap(prices: pd.DataFrame, out_path: Path, **kwargs) -> None:
    """Render a zone × time price heatmap and save to *out_path*."""
    fig, ax = plt.subplots(figsize=kwargs.get("figsize", (12, 4)))
    im = ax.imshow(prices.T.values, aspect="auto", interpolation="nearest")
    ax.set_yticks(range(len(prices.columns)))
    ax.set_yticklabels(prices.columns)
    fig.colorbar(im, ax=ax, label="€/MWh")
    ax.set_title("Price heatmap")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def save_duration_curves(prices: pd.DataFrame, out_path: Path, **kwargs) -> None:
    """Render per-zone price duration curves and save to *out_path*."""
    fig, ax = plt.subplots(figsize=kwargs.get("figsize", (8, 5)))
    for zone in sorted(prices.columns):
        vals = prices[zone].dropna().sort_values(ascending=False).reset_index(drop=True)
        ax.plot(vals.values, label=zone)
    ax.set_title("Price duration curves")
    ax.set_xlabel("Hour rank")
    ax.set_ylabel("€/MWh")
    ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)