#!/usr/bin/env python3
"""Script 5b: flow-based congestion model for Norwegian internal interconnects.

For each hour, computes utilization of each zone-pair interconnect as
abs(observed_flow) / NTC. When utilization >= 95%, the line is considered
congested and the shadow price is set to the observed price spread between
the two zones (zone_a price minus zone_b price, where zone_a is the left
side of the pair name). Otherwise, shadow price is zero.

When supplemental data is available (temperature, hydro reservoir), a second
output explains *why* a spread occurred: which zone was long/short, whether
demand was elevated, and whether hydro surplus was the source.

Outputs:
  artifacts/tables/congestion_shadow_prices.parquet  — shadow price per pair/hour
  artifacts/tables/congestion_attribution.parquet    — causal breakdown per congested hour
"""
from __future__ import annotations

import argparse

import pandas as pd

from baseload.pipeline_utils import ensure_dirs, load_config
from baseload.io import read_prices, read_transmission, read_load, read_gen, read_external_balance, write_parquet
from baseload.zones import DEFAULT_NTC_MW as NTC_MW, DEFAULT_ZONES as ZONES, ntc_from_cfg, pairs_from_cfg, zones_from_cfg

CONGESTION_THRESHOLD = 0.95

# Rolling window for "normal" temperature baseline (used to detect demand spikes).
TEMP_ROLLING_DAYS = 14


def run_congestion_model(
    prices: pd.DataFrame,
    transmission: pd.DataFrame,
    ntc: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Compute shadow prices from observed flows and NTC limits.

    A pair is congested when abs(flow) / NTC >= CONGESTION_THRESHOLD.
    The shadow price for a congested pair equals prices[zone_a] - prices[zone_b].

    Returns a DataFrame indexed by timestamp with one column per zone pair.
    """
    if ntc is None:
        ntc = NTC_MW
    prices, transmission = prices.align(transmission, join="inner", axis=0)
    result = pd.DataFrame(index=prices.index, columns=list(ntc.keys()), dtype=float)

    for pair, ntc_mw in ntc.items():
        parts = pair.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid NTC pair format: '{pair}'. Expected 'ZONE_A-ZONE_B'.")
        zone_a, zone_b = parts

        if pair not in transmission.columns:
            print(f"  WARNING: Transmission column '{pair}' not found — skipping (shadow price = 0).")
            result[pair] = 0.0
            continue
        if zone_a not in prices.columns or zone_b not in prices.columns:
            print(f"  WARNING: Price columns missing for {pair} ({zone_a}/{zone_b}) — skipping.")
            result[pair] = 0.0
            continue

        flow = transmission[pair]
        if flow.isna().all():
            print(f"  WARNING: Transmission flow for '{pair}' is all NaN — skipping.")
            result[pair] = 0.0
            continue

        utilization = flow.abs() / ntc_mw
        congested = utilization >= CONGESTION_THRESHOLD
        spread = prices[zone_a] - prices[zone_b]
        result[pair] = spread.where(congested, other=0.0)

    return result


def compute_net_positions(
    load: pd.DataFrame,
    actgen: pd.DataFrame,
    external_balance: pd.DataFrame,
    zones: list[str] | None = None,
) -> pd.DataFrame:
    """Return net position per zone (MW): generation + imports - load.

    Positive = zone is long (surplus); negative = zone is short (deficit).
    actgen has MultiIndex columns (zone, production_type); we sum across types.
    """
    if zones is None:
        zones = ZONES
    # Sum all generation types per zone.
    if isinstance(actgen.columns, pd.MultiIndex):
        gen_by_zone = actgen.T.groupby(level=0).sum().T  # axis=1 groupby deprecated in pandas 2.0
    else:
        gen_by_zone = actgen.copy()

    common_idx = load.index.intersection(gen_by_zone.index).intersection(external_balance.index)
    net = pd.DataFrame(index=common_idx, columns=zones, dtype=float)

    for zone in zones:
        g = gen_by_zone[zone] if zone in gen_by_zone.columns else pd.Series(0.0, index=common_idx)
        l = load[zone] if zone in load.columns else pd.Series(0.0, index=common_idx)
        e = external_balance[zone] if zone in external_balance.columns else pd.Series(0.0, index=common_idx)
        net[zone] = (g + e - l).reindex(common_idx)

    net.index.name = "time"
    return net


def build_attribution(
    shadow_prices: pd.DataFrame,
    transmission: pd.DataFrame,
    net_positions: pd.DataFrame,
    temperature: pd.DataFrame | None = None,
    reservoir: pd.DataFrame | None = None,
    cold_snap_threshold_c: float = -3.0,
    hydro_surplus_threshold_pct: float = 70.0,
    ntc: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build a long-format causal attribution table for congested hours.

    Returns one row per (timestamp, zone_pair) where congestion occurred,
    with columns explaining the direction of imbalance and likely cause.

    Fully vectorized — no per-row Python loops.
    """
    if ntc is None:
        ntc = NTC_MW
    # Precompute temperature deviation once (trailing 14-day baseline per zone).
    temp_deviation: pd.DataFrame | None = None
    if temperature is not None:
        baseline = temperature.rolling(window=TEMP_ROLLING_DAYS * 24, min_periods=24, center=False).mean()
        temp_deviation = temperature - baseline

    pair_frames: list[pd.DataFrame] = []

    for pair, ntc_mw in ntc.items():
        zone_a, zone_b = pair.split("-")

        if pair not in transmission.columns:
            continue

        flow = transmission[pair].reindex(shadow_prices.index)
        utilization = (flow.abs() / ntc_mw).round(3)
        shadow = shadow_prices[pair]
        congested_mask = shadow.abs() > 0.0

        if not congested_mask.any():
            continue

        idx = shadow_prices.index[congested_mask]

        # --- Core frame ---------------------------------------------------------
        df = pd.DataFrame(index=idx)
        df["zone_pair"] = pair
        df["shadow_price_eur_mwh"] = shadow[congested_mask].round(2).values
        df["utilization"] = utilization[congested_mask].values
        df["flow_mw"] = flow[congested_mask].round(1).values

        # --- Net positions ------------------------------------------------------
        pos_a = net_positions[zone_a].reindex(idx) if zone_a in net_positions.columns else pd.Series(float("nan"), index=idx)
        pos_b = net_positions[zone_b].reindex(idx) if zone_b in net_positions.columns else pd.Series(float("nan"), index=idx)
        df[f"{zone_a}_net_pos_mw"] = pos_a.round(1).values
        df[f"{zone_b}_net_pos_mw"] = pos_b.round(1).values

        # --- Long/short zone (vectorized) ---------------------------------------
        has_positions = ~(pos_a.isna() | pos_b.isna())
        a_is_long_pos = pos_a > pos_b
        a_is_long_flow = flow.reindex(idx) > 0   # fallback: exporting zone is long
        a_is_long = (has_positions & a_is_long_pos) | (~has_positions & a_is_long_flow)

        df["long_zone"] = pd.Series(
            [zone_a if v else zone_b for v in a_is_long], index=idx
        )
        df["short_zone"] = pd.Series(
            [zone_b if v else zone_a for v in a_is_long], index=idx
        )

        # --- Supplemental columns -----------------------------------------------
        if temperature is not None:
            for z in [zone_a, zone_b]:
                if z in temperature.columns:
                    df[f"{z}_temp_c"] = temperature[z].reindex(idx).round(1).values

        if reservoir is not None:
            for z in [zone_a, zone_b]:
                if z in reservoir.columns:
                    df[f"{z}_reservoir_pct"] = reservoir[z].reindex(idx).round(1).values

        # --- Cause (vectorized) -------------------------------------------------
        # Base cause from net positions.
        long_pos = pd.Series(
            [pos_a.iloc[i] if a_is_long.iloc[i] else pos_b.iloc[i] for i in range(len(idx))],
            index=idx,
        )
        short_pos = pd.Series(
            [pos_b.iloc[i] if a_is_long.iloc[i] else pos_a.iloc[i] for i in range(len(idx))],
            index=idx,
        )
        cause = pd.Series("congested", index=idx)
        cause[long_pos > 0] = "surplus_blocked"
        cause[(long_pos <= 0) & (short_pos < 0)] = "import_deficit"

        # Hydro surplus upgrade (long zone reservoir > threshold).
        if reservoir is not None:
            for z in [zone_a, zone_b]:
                if z in reservoir.columns:
                    res = reservoir[z].reindex(idx)
                    is_long_z = df["long_zone"] == z
                    cause[is_long_z & (res >= hydro_surplus_threshold_pct)] = "hydro_surplus"

        # Cold snap upgrade (short zone temp < baseline by threshold AND below heating threshold).
        # Heating demand is only significant in Norway when absolute temperature is below ~12°C.
        HEATING_THRESHOLD_C = 12.0
        if temp_deviation is not None and temperature is not None:
            for z in [zone_a, zone_b]:
                if z in temp_deviation.columns and z in temperature.columns:
                    dev = temp_deviation[z].reindex(idx)
                    abs_temp = temperature[z].reindex(idx)
                    is_short_z = df["short_zone"] == z
                    cold_mask = (
                        is_short_z
                        & (dev <= cold_snap_threshold_c)
                        & (abs_temp < HEATING_THRESHOLD_C)
                    )
                    # Combine with existing hydro_surplus if both apply.
                    was_hydro = cause == "hydro_surplus"
                    cause[cold_mask & was_hydro] = "hydro_surplus+cold_snap"
                    cause[cold_mask & ~was_hydro] = "cold_snap"

        df["cause"] = cause.values
        pair_frames.append(df)

    if not pair_frames:
        return pd.DataFrame()

    result = pd.concat(pair_frames).sort_index()
    result.index.name = "time"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    zones = zones_from_cfg(cfg)
    pairs = pairs_from_cfg(cfg)
    ntc = ntc_from_cfg(cfg)

    print("Loading core inputs...")
    prices = read_prices(paths, zones=zones)
    transmission = read_transmission(paths, pairs=pairs)

    # Load supplemental inputs if they exist — validated through io wrappers.
    def _try_load(reader, name: str) -> pd.DataFrame | None:
        try:
            df = reader()
            print(f"  Found {name}.parquet")
            return df
        except FileNotFoundError:
            return None

    load_df     = _try_load(lambda: read_load(paths, zones=zones), "load")
    actgen_df   = _try_load(lambda: read_gen(paths, zones=zones), "actgen")
    ext_bal_df  = _try_load(lambda: read_external_balance(paths, zones=zones), "external_balance")
    # temperature / hydro_reservoir have no io wrapper — read the parquet directly.
    temperature_df = _try_load(lambda: pd.read_parquet(paths["processed"] / "temperature.parquet"), "temperature")
    reservoir_df = _try_load(lambda: pd.read_parquet(paths["processed"] / "hydro_reservoir.parquet"), "hydro_reservoir")

    print(f"\nRunning flow-based congestion model for {len(prices)} hours...")
    shadow_prices = run_congestion_model(prices, transmission, ntc=ntc)

    out_path = paths["tables"] / "congestion_shadow_prices.parquet"
    write_parquet(shadow_prices, out_path)
    print(f"Saved shadow prices -> {out_path}")
    print("Congested hours per pair (utilization >= 95%):")
    for col in shadow_prices.columns:
        n_congested = (shadow_prices[col].abs() > 0.0).sum()
        print(f"  {col}: {n_congested} hours ({100 * n_congested / len(shadow_prices):.1f}%)")

    # --- Attribution -----------------------------------------------------------
    if load_df is not None and actgen_df is not None and ext_bal_df is not None:
        print("\nBuilding causal attribution...")

        # actgen MultiIndex columns may be stored as tuples — restore MultiIndex if flat.
        if not isinstance(actgen_df.columns, pd.MultiIndex):
            try:
                actgen_df.columns = pd.MultiIndex.from_tuples(
                    [eval(c) if isinstance(c, str) and c.startswith("(") else c for c in actgen_df.columns]
                )
                print("  Restored MultiIndex on actgen columns.")
            except Exception as exc:
                print(f"  WARNING: Could not restore actgen MultiIndex: {exc}")
                print(f"  Columns (first 5): {list(actgen_df.columns[:5])}")
                print("  Net positions will use flat columns — zone grouping may be inaccurate.")

        net_pos = compute_net_positions(load_df, actgen_df, ext_bal_df, zones=zones)

        sup_cfg = cfg.get("supplemental", {})
        attribution = build_attribution(
            shadow_prices,
            transmission,
            net_pos,
            temperature=temperature_df,
            reservoir=reservoir_df,
            cold_snap_threshold_c=float(sup_cfg.get("cold_snap_threshold_c", -3.0)),
            hydro_surplus_threshold_pct=float(sup_cfg.get("hydro_surplus_threshold_pct", 70.0)),
            ntc=ntc,
        )

        if not attribution.empty:
            attr_path = paths["tables"] / "congestion_attribution.parquet"
            write_parquet(attribution, attr_path)
            print(f"Saved attribution -> {attr_path}")
            print(f"  {len(attribution)} congested hour-pair records")

            # Print cause breakdown.
            cause_counts = attribution["cause"].value_counts()
            print("  Top causes:")
            for cause, count in cause_counts.head(10).items():
                print(f"    {cause}: {count} hours")
        else:
            print("  No congested hours found — attribution table empty.")
    else:
        missing = [n for n, v in [("load", load_df), ("actgen", actgen_df), ("external_balance", ext_bal_df)] if v is None]
        print(f"\nSkipping attribution — missing: {', '.join(missing)}")
        print("  Run ingest_entsoe.py first, then re-run this script.")


if __name__ == "__main__":
    main()
