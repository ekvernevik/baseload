# Regime Detection — Model & Validation (issue #17)

## What changed

`regime_clustering.py` no longer emits bare k-means price buckets. The model now:

1. **Incorporates physical drivers.** The daily feature table adds, when the
   corresponding processed parquet exists:
   - `wind_share` — wind generation / total generation from `actgen.parquet`
     (present in every standard pipeline run, so at least one physical driver
     is always active);
   - `hydro_fill_pct` — mean reservoir fill from `hydro_reservoir.parquet`
     (`ingest_supplemental.py`);
   - `temperature` — from `temperature.parquet` when a temperature feed is added.
   Features are standardised before clustering; the previous raw-scale fit was
   dominated by mean price and effectively ignored share-type features.
2. **Quantifies assignment uncertainty.** A `GaussianMixture` initialised from
   the k-means centroids yields `p_regime_j` per day plus a `confidence`
   score (max membership). Downstream consumers (`alerts_and_memo.py`) can
   discount alerts on low-confidence days.
3. **Produces a forward-looking signal.** A Laplace-smoothed Markov transition
   matrix over the daily label sequence gives
   `P(regime_{t+1} = j) = Σ_i P(regime_t = i) · P(j|i)`, written per day to
   `regime_forecast.parquet`. This is the scenario-weighting hook for the
   forecast ensemble (issue #18) and stochastic MPC (issue #19).

## Validation approach

Two layers, both reproducible:

### 1. Synthetic ground truth (automated, `tests/test_regime_clustering.py`)

A synthetic year alternates between a wet/windy oversupply regime (low price,
high wind, negative-price dips) and a scarcity regime (high price, low wind)
in 30-day blocks. The tests assert:

| Check | Threshold | Rationale |
|---|---|---|
| Adjusted Rand vs ground truth | > 0.8 | regimes recover the true blocks, not noise |
| Next-day forecast accuracy | > 0.9 | the forward signal actually predicts |
| Transition-matrix diagonal | > 0.85 | regimes persist (cold snaps don't restart at midnight) |
| Mean run length vs shuffled labels | > 3× | persistence is a property of the data, not the model |
| Silhouette | > 0.2 | clusters are separated in feature space |

### 2. Historical event alignment (per pipeline run, `regime_validation.parquet`)

Every run of `regime_clustering.py` writes:

- `mean_run_length_days` vs `mean_run_length_shuffled` — persistence check on
  real data;
- `silhouette` — separation quality;
- `winter_high_price_alignment` — share of the highest-price regime's days in
  Nov–Mar. Nordic scarcity regimes are winter phenomena (e.g. the Jan 2024 and
  Jan 2026 cold snaps); a value near the ~42 % calendar baseline means the
  "scarcity" cluster is not capturing scarcity.
- `mean_confidence`, `low_confidence_share` — how often the soft assignment is
  ambiguous (< 0.6 max membership).

When validating a new data year, eyeball `regime_calendar.png` against known
events (cold snaps, high-inflow summers, cable outages) and check that the
matching days sit in the expected regime with high confidence.

## Interface notes for downstream work

- `regime_profiles.parquet` keeps its previous shape (per-cluster feature
  means incl. `neg_price_share`) — `alerts_and_memo.py` is unaffected.
- `regime_daily.parquet` and `regime_forecast.parquet` are the new
  probabilistic contract for regime-conditional forecasting/dispatch
  (issues #18/#19): consumers should use `p_regime_j` / `p_next_regime_j`,
  not the hard `cluster` label.
