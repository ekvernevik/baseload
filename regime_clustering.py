#!/usr/bin/env python3
"""Script 5: market regime detection.

Upgrades over the original static k-means (issue #17):

- **Physical drivers as features.** Besides price-derived features, the daily
  feature table includes wind generation share (from actgen — always available
  in the standard pipeline), hydro reservoir fill (from
  ``hydro_reservoir.parquet`` when ``ingest_supplemental.py`` has run) and
  temperature (from ``temperature.parquet`` when present). Regimes therefore
  separate e.g. "wet + windy oversupply" from "cold-snap scarcity" instead of
  just price buckets.
- **Soft, uncertainty-aware assignment.** A Gaussian Mixture Model initialised
  from the k-means centroids gives every day a probability per regime and a
  confidence score (max probability). Days near a regime boundary are flagged
  as low-confidence instead of silently hard-labelled.
- **Forward-looking signal.** A Laplace-smoothed Markov transition matrix over
  the label sequence produces next-period regime probabilities
  (``regime_forecast.parquet``) — a real forecast, not just historical labels.
- **Validation.** Persistence (mean run length vs. shuffled labels), silhouette
  score, and seasonal alignment are written to
  ``artifacts/tables/regime_validation.parquet`` and summarised in
  docs/regime_validation.md; recovery of known regimes is exercised in
  tests/test_regime_clustering.py.

Outputs (artifacts/tables/):
  regime_profiles.parquet     per-cluster feature means (downstream contract:
                              alerts_and_memo.py reads `neg_price_share`)
  regime_daily.parquet        daily features + hard label + P(regime_j) + confidence
  regime_transitions.parquet  k×k Markov transition matrix
  regime_forecast.parquet     next-period regime probabilities per day
  regime_validation.parquet   validation metrics
"""
from __future__ import annotations

import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from baseload.pipeline_utils import ensure_dirs, load_config, resolution_freq
from baseload.zones import zones_from_cfg
from baseload.io import read_prices, write_parquet

WIND_TYPES = ["Wind Onshore", "Wind Offshore"]


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------

def build_daily_features(
    prices: pd.DataFrame,
    actgen: pd.DataFrame | None = None,
    hydro_reservoir: pd.DataFrame | None = None,
    temperature: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Daily regime features: price-derived plus available physical drivers.

    Every physical input is optional; whatever is present is appended. Wind
    share (from actgen) is the baseline physical driver because actgen ships
    with the standard ingest.
    """
    base = prices.mean(axis=1)
    daily = pd.DataFrame(index=base.resample("D").mean().index)
    daily["daily_mean_price"] = base.resample("D").mean()
    daily["daily_iqr"] = base.resample("D").quantile(0.75) - base.resample("D").quantile(0.25)
    daily["neg_price_share"] = (base < 0).resample("D").mean()

    # Spread features separate congested from uncongested days even when mean
    # prices are similar across zones.
    if prices.shape[1] >= 2:
        a, b = sorted(prices.columns)[:2]
        spread = (prices[a] - prices[b]).abs()
        daily["p95_spread"] = spread.resample("D").quantile(0.95)
        daily["persistence_proxy"] = (spread > spread.quantile(0.8)).resample("D").mean()
    else:
        daily["p95_spread"] = 0.0
        daily["persistence_proxy"] = 0.0

    # --- physical drivers ----------------------------------------------------
    if actgen is not None and isinstance(actgen.columns, pd.MultiIndex):
        total = actgen.T.groupby(level=0).sum().T.sum(axis=1)
        wind_cols = [c for c in actgen.columns if c[1] in WIND_TYPES]
        if wind_cols and total.max() > 0:
            wind = actgen[wind_cols].sum(axis=1)
            share = (wind / total.replace(0.0, np.nan)).clip(0, 1)
            daily["wind_share"] = share.resample("D").mean()

    if hydro_reservoir is not None and len(hydro_reservoir):
        daily["hydro_fill_pct"] = hydro_reservoir.mean(axis=1).resample("D").mean()

    if temperature is not None and len(temperature):
        temp = temperature.mean(axis=1) if isinstance(temperature, pd.DataFrame) else temperature
        daily["temperature"] = temp.resample("D").mean()

    return daily.dropna(how="all").fillna(0.0)


def physical_drivers_present(daily: pd.DataFrame) -> list[str]:
    return [c for c in ("wind_share", "hydro_fill_pct", "temperature") if c in daily.columns]


# ---------------------------------------------------------------------------
# Regime model
# ---------------------------------------------------------------------------

def fit_regimes(daily: pd.DataFrame, k: int = 3, seed: int = 42) -> pd.DataFrame:
    """Fit k-means labels + GMM soft probabilities on standardised features.

    Returns *daily* extended with ``cluster`` (hard label), ``p_regime_<j>``
    (soft membership) and ``confidence`` (max membership probability).
    Features are standardised first — raw-scale k-means is dominated by the
    largest-magnitude feature (mean price) and ignores shares in [0, 1].
    """
    feature_cols = [c for c in daily.columns if c != "cluster"]
    X = StandardScaler().fit_transform(daily[feature_cols].values)

    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    labels = km.fit_predict(X)

    # GMM initialised from the k-means solution so component j == cluster j;
    # gives soft memberships without relabelling ambiguity.
    gmm = GaussianMixture(
        n_components=k,
        covariance_type="diag",
        means_init=km.cluster_centers_,
        random_state=seed,
        reg_covar=1e-4,
    )
    gmm.fit(X)
    probs = gmm.predict_proba(X)

    out = daily.copy()
    out["cluster"] = labels
    for j in range(k):
        out[f"p_regime_{j}"] = probs[:, j]
    out["confidence"] = probs.max(axis=1)
    return out


def transition_matrix(labels: pd.Series | np.ndarray, k: int) -> pd.DataFrame:
    """Laplace-smoothed first-order Markov transition matrix P[i, j] =
    P(regime_{t+1} = j | regime_t = i)."""
    lab = np.asarray(labels, dtype=int)
    counts = np.ones((k, k))  # Laplace prior: unseen transitions get small mass
    for a, b in zip(lab[:-1], lab[1:]):
        counts[a, b] += 1
    P = counts / counts.sum(axis=1, keepdims=True)
    return pd.DataFrame(
        P,
        index=[f"from_{i}" for i in range(k)],
        columns=[f"to_{j}" for j in range(k)],
    )


def forecast_next_regime(daily: pd.DataFrame, trans: pd.DataFrame, k: int) -> pd.DataFrame:
    """Forward-looking signal: for each day t, P(regime_{t+1} = j).

    Blends the Markov row for today's hard label with today's soft membership:
    P(next = j) = sum_i P(today = i) * P(j | i). Uncertain days therefore
    propagate their uncertainty into the forecast instead of betting on one row.
    """
    prob_cols = [f"p_regime_{j}" for j in range(k)]
    today = daily[prob_cols].values          # (T, k)
    P = trans.values                          # (k, k)
    nxt = today @ P                           # (T, k)
    out = pd.DataFrame(
        nxt, index=daily.index, columns=[f"p_next_regime_{j}" for j in range(k)]
    )
    out["expected_next_regime"] = nxt.argmax(axis=1)
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_regimes(daily: pd.DataFrame, k: int, seed: int = 42) -> pd.DataFrame:
    """Quantitative checks that the regimes behave like market regimes.

    - mean_run_length_days vs shuffled baseline: real regimes persist
      (a cold snap does not restart at midnight); shuffled labels don't.
    - silhouette: cluster separation quality in feature space.
    - winter_high_price_alignment: share of the highest-price regime's days
      falling in Nov-Mar — Nordic scarcity regimes are winter phenomena.
    """
    labels = daily["cluster"].values
    feature_cols = [
        c for c in daily.columns
        if not c.startswith(("p_regime_", "p_next_")) and c not in ("cluster", "confidence")
    ]
    X = StandardScaler().fit_transform(daily[feature_cols].values)

    def mean_run_length(lab: np.ndarray) -> float:
        runs, current = [], 1
        for a, b in zip(lab[:-1], lab[1:]):
            if a == b:
                current += 1
            else:
                runs.append(current)
                current = 1
        runs.append(current)
        return float(np.mean(runs))

    rng = np.random.default_rng(seed)
    shuffled = labels.copy()
    rng.shuffle(shuffled)

    sil = float(silhouette_score(X, labels)) if len(set(labels)) > 1 else float("nan")

    high_regime = int(daily.groupby("cluster")["daily_mean_price"].mean().idxmax())
    high_days = daily[daily["cluster"] == high_regime]
    winter = high_days.index.month.isin([11, 12, 1, 2, 3]).mean() if len(high_days) else float("nan")

    return pd.DataFrame([
        {"metric": "mean_run_length_days", "value": mean_run_length(labels)},
        {"metric": "mean_run_length_shuffled", "value": mean_run_length(shuffled)},
        {"metric": "silhouette", "value": sil},
        {"metric": "winter_high_price_alignment", "value": float(winter)},
        {"metric": "mean_confidence", "value": float(daily["confidence"].mean())},
        {"metric": "low_confidence_share", "value": float((daily["confidence"] < 0.6).mean())},
    ])


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = ensure_dirs(cfg)
    zones = zones_from_cfg(cfg)
    freq = resolution_freq(cfg)
    prices = read_prices(paths, zones=zones, freq=freq)

    # Optional physical inputs — use whatever the pipeline has produced.
    def _optional(name: str) -> pd.DataFrame | None:
        p = paths["processed"] / name
        return pd.read_parquet(p) if p.exists() else None

    actgen = _optional("actgen.parquet")
    hydro = _optional("hydro_reservoir.parquet")
    temperature = _optional("temperature.parquet")

    daily = build_daily_features(prices, actgen, hydro, temperature)
    drivers = physical_drivers_present(daily)
    if drivers:
        print(f"Physical drivers in feature set: {drivers}")
    else:
        print(
            "WARNING: no physical drivers available (no actgen/hydro/temperature "
            "parquet found) — regimes fall back to price-derived features only."
        )

    k = int(cfg.get("regime", {}).get("k", 3))
    # Fixed seed keeps regime labels deterministic across pipeline re-runs.
    seed = int(cfg.get("regime", {}).get("seed", 42))

    daily = fit_regimes(daily, k=k, seed=seed)
    trans = transition_matrix(daily["cluster"], k)
    forecast = forecast_next_regime(daily, trans, k)
    validation = validate_regimes(daily, k, seed=seed)

    profiles = daily.groupby("cluster").mean(numeric_only=True).reset_index().sort_values("cluster")
    write_parquet(profiles, paths["tables"] / "regime_profiles.parquet")
    write_parquet(daily, paths["tables"] / "regime_daily.parquet")
    write_parquet(trans, paths["tables"] / "regime_transitions.parquet")
    write_parquet(forecast, paths["tables"] / "regime_forecast.parquet")
    write_parquet(validation, paths["tables"] / "regime_validation.parquet")
    print("Validation metrics:")
    print(validation.to_string(index=False))

    plt.figure(figsize=(12, 2.8))
    plt.scatter(daily.index, daily["cluster"], c=daily["cluster"], cmap="tab10", s=10)
    plt.title("Regime calendar")
    plt.yticks(range(k))
    plt.tight_layout()
    plt.savefig(paths["figures"] / "regime_calendar.png", dpi=150)
    plt.close()

    # Confidence overlay: where the soft assignment is uncertain.
    plt.figure(figsize=(12, 2.8))
    plt.plot(daily.index, daily["confidence"], lw=0.8)
    plt.axhline(0.6, color="red", ls="--", lw=0.8)
    plt.title("Regime assignment confidence (max GMM membership)")
    plt.tight_layout()
    plt.savefig(paths["figures"] / "regime_confidence.png", dpi=150)
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
