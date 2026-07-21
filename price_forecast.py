#!/usr/bin/env python3
"""Script 5b: probabilistic per-zone day-ahead price forecast (issue #18).

Emits, per zone:
  - P10/P50/P90 quantile bands for each delivery period, and
  - a scenario ensemble (day-block bootstrap around the P50 path) suitable for
    driving the stochastic rolling-horizon optimiser (issue #19) and the
    P50/P90 revenue methodology (issue #21).

Model: direct next-day quantile regression with gradient-boosted trees
(sklearn only, one model per quantile) on causal features — same-period price
lags at 1/2/7 days, previous-day statistics, and calendar encodings. Scenario
draws add bootstrapped whole-day residual paths to the P50 forecast, which
preserves intra-day correlation (a scenario is a coherent day, not independent
noise per period).

Backtest: rolling-origin, out-of-sample. For each backtest day the model is
refit on data strictly before it (weekly refit cadence), predicts the next
day, and the realised day is scored: RMSE on P50, pinball loss per quantile,
and empirical P10-P90 coverage. Reported per zone in
``artifacts/tables/forecast_backtest.parquet``.

Outputs:
  data/processed/price_forecast.parquet    (zone, band) MultiIndex columns
  data/processed/price_scenarios.parquet   (zone, scenario) MultiIndex columns
  artifacts/tables/forecast_backtest.parquet
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from baseload.io import read_prices, write_parquet
from baseload.pipeline_utils import index_dt_hours, init_pipeline, load_config

DEFAULT_QUANTILES = (0.1, 0.5, 0.9)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def periods_per_day(price: pd.Series) -> int:
    return max(int(round(24.0 / index_dt_hours(price.index))), 1)


def build_features(price: pd.Series) -> pd.DataFrame:
    """Causal feature matrix for direct next-day forecasting.

    Every feature at time t is known by day-ahead gate closure for t's
    delivery day: same-period lags ≥ 1 day and previous-day aggregates.
    """
    ppd = periods_per_day(price)
    feats = pd.DataFrame(index=price.index)
    feats["lag_1d"] = price.shift(ppd)
    feats["lag_2d"] = price.shift(2 * ppd)
    feats["lag_7d"] = price.shift(7 * ppd)
    prev_day_mean = price.rolling(ppd).mean().shift(ppd)
    prev_day_max = price.rolling(ppd).max().shift(ppd)
    prev_day_min = price.rolling(ppd).min().shift(ppd)
    feats["prev_day_mean"] = prev_day_mean
    feats["prev_day_spread"] = prev_day_max - prev_day_min

    hod = price.index.hour + price.index.minute / 60.0
    feats["hod_sin"] = np.sin(2 * np.pi * hod / 24.0)
    feats["hod_cos"] = np.cos(2 * np.pi * hod / 24.0)
    dow = price.index.dayofweek
    feats["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    feats["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    feats["is_weekend"] = (dow >= 5).astype(float)
    return feats


# ---------------------------------------------------------------------------
# Quantile forecaster
# ---------------------------------------------------------------------------

class QuantileForecaster:
    """Per-zone probabilistic day-ahead forecaster.

    fit() trains one gradient-boosted quantile model per requested quantile
    and stores the in-sample P50 residuals as whole-day paths for scenario
    generation. predict() emits sorted (non-crossing) quantile bands;
    scenarios() emits bootstrapped price paths around P50.
    """

    def __init__(
        self,
        quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
        n_estimators: int = 100,
        max_depth: int = 3,
        seed: int = 42,
    ) -> None:
        self.quantiles = tuple(sorted(quantiles))
        if 0.5 not in self.quantiles:
            raise ValueError("Quantile set must include 0.5 (P50 anchors the scenarios).")
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.seed = seed
        self.models: dict[float, GradientBoostingRegressor] = {}
        self.residual_days: np.ndarray | None = None   # (n_days, periods_per_day)
        self._ppd: int | None = None

    def fit(self, price: pd.Series) -> "QuantileForecaster":
        price = price.dropna()
        self._ppd = periods_per_day(price)
        feats = build_features(price)
        mask = feats.notna().all(axis=1)
        X, y = feats.loc[mask].values, price.loc[mask].values
        # The 7-day lag consumes the first week, so usable rows start at day 8;
        # require a further week of usable rows for a minimally sane fit.
        if len(y) < 7 * self._ppd:
            raise ValueError(
                f"Need at least 14 days of raw history (7 usable after lag trimming) "
                f"to fit the forecaster, got {len(y)} usable periods."
            )

        for q in self.quantiles:
            model = GradientBoostingRegressor(
                loss="quantile",
                alpha=q,
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                random_state=self.seed,
            )
            model.fit(X, y)
            self.models[q] = model

        # Whole-day residual paths (realised - P50) for block-bootstrap scenarios.
        resid = pd.Series(y - self.models[0.5].predict(X), index=price.loc[mask].index)
        by_day = resid.groupby(resid.index.date)
        full_days = [v.values for _, v in by_day if len(v) == self._ppd]
        self.residual_days = np.array(full_days) if full_days else np.zeros((1, self._ppd))
        return self

    def _target_features(self, history: pd.Series, target_index: pd.DatetimeIndex) -> np.ndarray:
        extended = pd.concat([
            history.dropna(),
            pd.Series(np.nan, index=target_index),
        ])
        extended = extended[~extended.index.duplicated(keep="first")].sort_index()
        feats = build_features(extended).loc[target_index]
        # Missing lags (short history) fall back to the last observed price level.
        return feats.fillna(float(history.dropna().iloc[-1])).values

    def predict(self, history: pd.Series, target_index: pd.DatetimeIndex) -> pd.DataFrame:
        """Quantile bands for *target_index*, using only *history* (causal)."""
        if not self.models:
            raise RuntimeError("fit() must be called first")
        X = self._target_features(history, target_index)
        raw = np.column_stack([self.models[q].predict(X) for q in self.quantiles])
        raw.sort(axis=1)  # enforce non-crossing quantiles
        cols = [f"p{int(q * 100)}" for q in self.quantiles]
        return pd.DataFrame(raw, index=target_index, columns=cols)

    def scenarios(
        self,
        history: pd.Series,
        target_index: pd.DatetimeIndex,
        n_scenarios: int = 50,
        seed: int | None = None,
    ) -> pd.DataFrame:
        """Scenario ensemble: P50 path + bootstrapped whole-day residual paths.

        Sampling full days preserves intra-day correlation structure; each
        scenario is a coherent price day. For multi-day targets, consecutive
        days get independently drawn residual days.
        """
        assert self.residual_days is not None and self._ppd is not None
        bands = self.predict(history, target_index)
        p50 = bands["p50"].values
        rng = np.random.default_rng(self.seed if seed is None else seed)

        n_periods = len(target_index)
        n_days = int(np.ceil(n_periods / self._ppd))
        draws = np.empty((n_periods, n_scenarios))
        for s in range(n_scenarios):
            day_ids = rng.integers(0, len(self.residual_days), size=n_days)
            path = np.concatenate([self.residual_days[d] for d in day_ids])[:n_periods]
            draws[:, s] = p50 + path
        cols = [f"s{s:03d}" for s in range(n_scenarios)]
        return pd.DataFrame(draws, index=target_index, columns=cols)


# ---------------------------------------------------------------------------
# Metrics + rolling-origin backtest
# ---------------------------------------------------------------------------

def pinball_loss(y: np.ndarray, pred: np.ndarray, q: float) -> float:
    diff = y - pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def backtest_zone(
    price: pd.Series,
    backtest_days: int = 90,
    refit_every_days: int = 7,
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES,
    n_scenarios: int = 50,
    seed: int = 42,
    n_estimators: int = 100,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Rolling-origin out-of-sample backtest for one zone.

    Returns (bands, scenarios, metrics): out-of-sample quantile bands and
    scenario draws for the backtest window, plus scoring metrics.
    """
    price = price.dropna()
    ppd = periods_per_day(price)
    dates = pd.DatetimeIndex(sorted(set(price.index.normalize())))
    if len(dates) <= backtest_days + 14:
        raise ValueError(
            f"Not enough history: {len(dates)} days available, "
            f"need > {backtest_days + 14} for a {backtest_days}-day backtest."
        )
    test_dates = dates[-backtest_days:]

    forecaster: QuantileForecaster | None = None
    band_parts, scen_parts = [], []
    for i, day in enumerate(test_dates):
        history = price[price.index < day]
        if forecaster is None or i % refit_every_days == 0:
            forecaster = QuantileForecaster(
                quantiles=quantiles, seed=seed, n_estimators=n_estimators
            ).fit(history)
        target_index = price.index[price.index.normalize() == day]
        if target_index.empty:
            continue
        band_parts.append(forecaster.predict(history, target_index))
        scen_parts.append(
            forecaster.scenarios(history, target_index, n_scenarios=n_scenarios, seed=seed + i)
        )

    bands = pd.concat(band_parts)
    scenarios = pd.concat(scen_parts)
    realised = price.reindex(bands.index)

    y = realised.values
    metrics = {
        "rmse": float(np.sqrt(np.mean((y - bands["p50"].values) ** 2))),
        "coverage_p10_p90": float(
            ((y >= bands.iloc[:, 0].values) & (y <= bands.iloc[:, -1].values)).mean()
        ),
        "n_periods": float(len(y)),
    }
    for q in quantiles:
        metrics[f"pinball_q{int(q * 100)}"] = pinball_loss(y, bands[f"p{int(q * 100)}"].values, q)
    return bands, scenarios, metrics


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = init_pipeline(cfg)
    prices = read_prices(paths)

    fcfg = cfg.get("forecast", {}) or {}
    quantiles = tuple(float(q) for q in fcfg.get("quantiles", DEFAULT_QUANTILES))
    n_scenarios = int(fcfg.get("n_scenarios", 50))
    backtest_days = int(fcfg.get("backtest_days", 90))
    seed = int(fcfg.get("seed", 42))

    all_bands, all_scens, rows = {}, {}, []
    for zone in sorted(prices.columns):
        print(f"  backtesting {zone} ({backtest_days} days out-of-sample)...", flush=True)
        bands, scens, metrics = backtest_zone(
            prices[zone],
            backtest_days=backtest_days,
            quantiles=quantiles,
            n_scenarios=n_scenarios,
            seed=seed,
        )
        for band in bands.columns:
            all_bands[(zone, band)] = bands[band]
        for s in scens.columns:
            all_scens[(zone, s)] = scens[s]
        rows.append({"zone": zone, **metrics})

    forecast_df = pd.DataFrame(all_bands)
    forecast_df.columns = pd.MultiIndex.from_tuples(forecast_df.columns, names=["_zone", "_band"])
    scen_df = pd.DataFrame(all_scens)
    scen_df.columns = pd.MultiIndex.from_tuples(scen_df.columns, names=["_zone", "_scenario"])

    write_parquet(forecast_df, paths["processed"] / "price_forecast.parquet", schema_name="price_forecast")
    write_parquet(scen_df, paths["processed"] / "price_scenarios.parquet", schema_name="price_scenarios")

    report = pd.DataFrame(rows)
    write_parquet(report, paths["tables"] / "forecast_backtest.parquet", schema_name="forecast_backtest", also_csv=True)
    print("\nOut-of-sample backtest report:")
    print(report.to_string(index=False))


if __name__ == "__main__":
    main()
