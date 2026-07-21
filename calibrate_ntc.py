#!/usr/bin/env python3
"""Script 4b: calibrate NTC values against observed congestion (issue #20).

The hardcoded public-doc NTCs make the transport model bind in the wrong
places (e.g. NO1-NO2 0% of the time, NO1-NO5 68%). This script fits per-link
effective NTCs from data instead:

Method
------
For each configured interconnector pair with both observed flows
(transmission.parquet) and zonal prices:

1. Observed congestion frequency f_obs = share of periods where the zones'
   day-ahead prices separate: |p_a - p_b| > `sep_threshold` (default 1 EUR/MWh;
   under zonal market coupling prices only separate when the interconnector
   capacity binds).
2. Calibrated NTC = the (1 - f_obs) quantile of |observed flow|. By
   construction, flow reaches/exceeds this limit with the same frequency as
   prices actually separate — the effective, not nameplate, limit.
3. Verification: modeled binding frequency (share of |flow| >= calibrated NTC)
   is compared with f_obs and must agree within `tolerance_pp` percentage
   points (config `calibration.tolerance_pp`, default 2.0).

Outputs
-------
artifacts/tables/ntc_calibration.parquet   per-pair fit + verification report
data/metadata/ntc_calibrated.yaml          drop-in `network.interconnectors`
                                           block with provenance — paste into
                                           the pipeline config (or point at it)
                                           to make norway_network.py use the
                                           calibrated limits.

A warning is raised when the data window is shorter than 2 years; the fit is
still produced (useful in tests/exploration) but flagged as provisional.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from baseload.io import read_prices, read_transmission, write_parquet
from baseload.pipeline_utils import init_pipeline, load_config

MIN_YEARS_RECOMMENDED = 2.0


def calibrate_pair(
    spread: pd.Series,
    flow: pd.Series,
    sep_threshold: float = 1.0,
) -> dict:
    """Fit one link's effective NTC from observed price separation + flows."""
    joined = pd.concat({"spread": spread, "flow": flow}, axis=1).dropna()
    if len(joined) < 168:
        raise ValueError(f"Too little overlapping data to calibrate ({len(joined)} periods).")

    abs_spread = joined["spread"].abs()
    abs_flow = joined["flow"].abs()

    f_obs = float((abs_spread > sep_threshold).mean())
    if f_obs <= 0.0:
        # Prices never separate: the link effectively never binds. Set the
        # limit above the observed flow envelope so the model never binds either.
        ntc = float(abs_flow.max() * 1.05)
    else:
        ntc = float(abs_flow.quantile(1.0 - f_obs))

    modeled = float((abs_flow >= ntc - 1e-9).mean())
    return {
        "observed_sep_freq": f_obs,
        "ntc_mw": ntc,
        "modeled_binding_freq": modeled,
        "abs_error_pp": abs(modeled - f_obs) * 100.0,
        "n_periods": float(len(joined)),
    }


def calibrate_ntc(
    prices: pd.DataFrame,
    flows: pd.DataFrame,
    pairs: list[str],
    sep_threshold: float = 1.0,
    tolerance_pp: float = 2.0,
) -> pd.DataFrame:
    """Calibrate every pair present in both the flow table and the price table."""
    rows = []
    for pair in pairs:
        zone_a, zone_b = pair.split("-")
        if pair not in flows.columns:
            print(f"  {pair}: no observed flow column — skipped.")
            continue
        if zone_a not in prices.columns or zone_b not in prices.columns:
            print(f"  {pair}: missing zone prices — skipped.")
            continue
        spread = prices[zone_a] - prices[zone_b]
        result = calibrate_pair(spread, flows[pair], sep_threshold=sep_threshold)
        result["pair"] = pair
        result["within_tolerance"] = float(result["abs_error_pp"] <= tolerance_pp)
        rows.append(result)

    if not rows:
        raise ValueError("No calibratable pairs (no overlap between config pairs, flows, and prices).")

    cols = ["pair", "ntc_mw", "observed_sep_freq", "modeled_binding_freq",
            "abs_error_pp", "within_tolerance", "n_periods"]
    return pd.DataFrame(rows)[cols]


def emit_config_block(report: pd.DataFrame, provenance: dict, out_path: Path) -> None:
    """Write a drop-in `network.interconnectors` YAML block with provenance."""
    block = {
        "network": {
            "interconnectors": {
                row["pair"]: {
                    "ntc_mw": round(float(row["ntc_mw"]), 1),
                    "calibrated": True,
                    "source": (
                        f"calibrate_ntc.py {provenance['generated']} | "
                        f"window {provenance['data_start']}..{provenance['data_end']} | "
                        f"sep_threshold={provenance['sep_threshold']} EUR/MWh | "
                        f"binding {row['modeled_binding_freq']:.1%} vs observed {row['observed_sep_freq']:.1%}"
                    ),
                }
                for _, row in report.iterrows()
            }
        }
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(block, fh, sort_keys=False, width=140)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = init_pipeline(cfg)
    prices = read_prices(paths)
    flows = read_transmission(paths)

    cal_cfg = cfg.get("calibration", {}) or {}
    sep_threshold = float(cal_cfg.get("sep_threshold_eur", 1.0))
    tolerance_pp = float(cal_cfg.get("tolerance_pp", 2.0))

    inter = (cfg.get("network") or {}).get("interconnectors") or {}
    pairs = list(inter) if inter else list(flows.columns)

    years = (prices.index.max() - prices.index.min()).days / 365.25
    if years < MIN_YEARS_RECOMMENDED:
        print(
            f"WARNING: calibration window is {years:.1f} years (< {MIN_YEARS_RECOMMENDED:.0f}y "
            "recommended) — treat the fitted NTCs as provisional."
        )

    report = calibrate_ntc(prices, flows, pairs, sep_threshold=sep_threshold, tolerance_pp=tolerance_pp)

    provenance = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "data_start": str(prices.index.min().date()),
        "data_end": str(prices.index.max().date()),
        "sep_threshold": sep_threshold,
        "window_years": round(years, 2),
    }

    write_parquet(report, paths["tables"] / "ntc_calibration.parquet", also_csv=True)
    yaml_path = paths["metadata"] / "ntc_calibrated.yaml"
    emit_config_block(report, provenance, yaml_path)

    print("\nNTC calibration report:")
    print(report.to_string(index=False))
    print(f"\nDrop-in config block -> {yaml_path}")
    bad = report[report["within_tolerance"] < 1.0]
    if len(bad):
        print(
            f"WARNING: {len(bad)} pair(s) outside ±{tolerance_pp}pp tolerance: "
            f"{', '.join(bad['pair'])} — inspect flow data quality for these links."
        )


if __name__ == "__main__":
    main()
