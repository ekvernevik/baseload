# Ensemble Approaches to Regime Detection — 30-Min Discussion

**Prepared: 2026-07-10 | Duration: 30 min | Audience: team working on `regime_clustering.py` and downstream BESS/memo pipeline**

## Context (read before the meeting, not during)

Current implementation: [`regime_clustering.py`](../regime_clustering.py) — a single deterministic `KMeans(k=3, random_state=42)` fit on 5 daily features (`daily_mean_price`, `daily_iqr`, `neg_price_share`, `p95_spread`, `persistence_proxy`), each day clustered independently with no temporal/persistence model. Output (`regime_profiles.parquet`) feeds exactly one downstream consumer: the oversupply alert in `alerts_and_memo.py`. It is not fed back into `bess_valuation_pf.py` / `bess_valuation_rh.py` — there is no regime-conditional dispatch today.

`docs/strategic_review_2026-04-19.md` already flagged this as the weakest "differentiator" claim (2/10 defensibility): no hydro reservoir state, no wind, no temperature, no forecasting of future regimes, single fixed-seed fit with no notion of model uncertainty. This meeting is about what "ensemble" should mean here and whether it's worth building before the next review cycle.

---

## Agenda

### 1. Frame the problem — what are we actually ensembling? (5 min)

Three distinct things get called "ensemble" and we should not conflate them:

- **A. Ensemble of clusterings** — same k-means (or different k, different seeds, different feature subsets) run N times, labels reconciled via consensus/co-association matrix, to get a stability-scored regime assignment instead of one brittle deterministic label.
- **B. Ensemble of models** — different algorithm families (k-means, Gaussian Mixture Model, Hidden Markov Model, changepoint detection) voting or stacked, since each encodes a different assumption (k-means: spherical/no persistence; GMM: soft assignment/uncertainty; HMM: temporal persistence and transition structure — currently entirely absent from the codebase).
- **C. Ensemble of forecasts conditioned on regime** — this is the one `strategic_review` actually calls out (Claim 3: rolling-horizon BESS uses perfect-foresight prices, no scenario ensemble, no CVaR). Regime detection would supply the *scenario weights* for a price-forecast ensemble feeding BESS dispatch.

Decide up front: is today's discussion about hardening the regime *labels themselves* (A/B), or about using regimes to drive a downstream *decision-uncertainty* ensemble (C)? These have different owners and different urgency. Recommend spending most of the time on B (it's the direct fix to the 2/10 defensibility gap) and closing with C (it's the higher-value but bigger lift, ties to the BESS MPC gap).

### 2. Approach comparison (12 min)

Walk through with a shared framework: *what failure mode of the current k-means does each approach fix, and what does it cost?*

| Approach | Fixes | Cost / risk | Fits current pipeline? |
|---|---|---|---|
| **Bagged k-means (bootstrap resample + consensus clustering)** | Instability of a single fit — quantifies "how confident are we this day is regime 2" | Cheap, ~20 lines added to `regime_clustering.py`, no new dependency (sklearn only) | Yes — drop-in, same feature set |
| **Multi-k / multi-seed ensemble + co-association matrix → hierarchical merge** | Arbitrary fixed `k=3`; lets data suggest natural cluster count | Slightly more compute, still sklearn-only | Yes |
| **GMM as a second voting member** | Hard-boundary artifact of k-means (e.g., a day right at a price threshold flips regimes on noise); gives soft/probabilistic regime membership | New dependency already available (`sklearn.mixture.GaussianMixture`), minor | Yes |
| **HMM (e.g. `hmmlearn` GaussianHMM)** | The biggest real gap: **no persistence/transition structure**. Regimes should have inertia (cold snap doesn't end and restart every midnight); HMM models P(regime\_t \| regime\_{t-1}) | New dependency, more design work (choosing emission distribution, initialization), harder to explain in the memo | Would need to become a real ensemble *member* combined with k-means/GMM, not a replacement — mixing a sequential model with i.i.d. cluster votes needs a defined reconciliation rule |
| **Changepoint detection (e.g. `ruptures`) as a regime-boundary prior** | Regimes today can flicker day-to-day since there's no smoothing; changepoint detection could constrain *where* transitions are allowed to occur, which an ensemble vote then labels | New dependency, another design axis (penalty tuning) | Complementary to, not a replacement for, cluster-based labeling |
| **Feature-space ensemble (bagging across feature subsets, not just resamples)** | The "three scalar features" critique — different subsets each capture congestion vs. price-level vs. persistence; ensembling across feature subsets is cheaper than adding new raw data sources (hydro/wind/temp) and buys some robustness now | Needs subsets that are actually decorrelated to be meaningful — current 5 features aren't obviously decomposable into independent groups | Yes, no new data needed |

Discussion prompt: which of these is worth doing *before* adding new raw features (hydro reservoir, wind, temperature — flagged as absent in the strategic review) versus which only pays off *after* richer features exist? Bagged k-means / GMM voting are useful regardless of feature set. HMM and changepoint detection get much more valuable once there's a genuinely time-structured signal (e.g. hydro reservoir drawdown) to model persistence over — doing HMM now on 5 price-derived features may just be modeling autocorrelation in price itself, which is a weaker claim.

### 3. Reconciliation & scoring — how does an ensemble actually output *one* regime label? (5 min)

This is the part most likely to get hand-waved and needs explicit agreement:
- Consensus clustering / co-association matrix + agglomerative merge (standard, explainable) vs. simple majority vote (loses information about disagreement, which is often the most useful output — a "confidence: low" day is more actionable than a wrong hard label).
- Where does regime *disagreement* itself become a feature? E.g., `alerts_and_memo.py`'s oversupply alert currently trusts `neg_price_share` from one point estimate — an ensemble should probably surface a confidence/agreement score that the memo can flag ("regime uncertain, treat oversupply alert with caution") rather than silently picking a winner.
- Does regime output become probabilistic (soft assignment, e.g. `P(regime=oversupply)=0.7`) and does that change the downstream contract with `alerts_and_memo.py` and any future regime-conditional BESS dispatch? This is a real interface decision, not just a modeling one.

### 4. Decide + assign (5 min)

Land on:
- One small ensemble change to prototype before the next review (bagged k-means + GMM vote is the recommended cheapest first step — reuses existing features/deps, directly answers the "First-year grad student clustering" critique).
- Whether HMM/changepoint work is explicitly deferred pending new features (hydro/wind/temp) or worth a parallel spike now.
- Owner for wiring ensemble output (soft labels / confidence) into `alerts_and_memo.py`, since that's the only current consumer and the interface change ripples there first.
- Whether "regime → BESS dispatch conditioning" (item C above) gets its own follow-up meeting — it's coupled to the separate, larger rolling-horizon/perfect-foresight gap already logged in the strategic review and probably shouldn't be decided as a side effect of this one.

### 5. Buffer / parking lot (3 min)

---

## Interesting research/work that remains open

Grouped by how directly it depends on today's decisions:

**Directly blocks a real ensemble (near-term):**
- No ground truth / no evaluation metric for regime quality exists anywhere — there's no `test_regime_clustering.py` and no labeled or externally-validated regime calendar to score cluster stability or ensemble agreement against. Any ensemble work needs a validation approach (e.g. silhouette/stability score across bootstrap runs, or back-testing regime persistence against known events like cold snaps) before "ensemble improves things" is a testable claim rather than an assertion.
- Feature set is price-derived only; there is no hydro reservoir, wind output, or temperature data ingested anywhere in the pipeline (confirmed absent — no matching ingest script). Ensembling over 5 correlated price-quantile features has a ceiling; the highest-leverage open research question is probably *what new data source most changes the regime picture*, not *which ensembling algorithm*.
- No temporal/persistence structure today (each day is i.i.d. to the clusterer). Whether HMM-based ensembling is worth the complexity depends on whether regimes in this market are actually persistent multi-day phenomena — that's an empirical question nobody has looked at yet (e.g., autocorrelation of current k-means labels day-to-day).

**Downstream, higher-value, larger scope (medium-term):**
- Regime-conditional forecasting/dispatch: today regime labels don't feed `bess_valuation_pf.py`/`bess_valuation_rh.py` at all. The strategic review's Claim 3 gap (rolling-horizon uses perfect foresight, no scenario ensemble, no CVaR) is the actual product-level ensemble opportunity — a price-scenario ensemble conditioned on regime state, feeding a stochastic MPC. This is materially bigger than hardening the clustering itself and probably deserves its own planning session once the labeling-side ensemble work above lands.
- Out-of-sample validation of regimes across years — current fixed-seed single-year fit has no versioning or drift-monitoring story; an ensemble that's stable in-sample could still drift regime definitions season to season with no detection mechanism in place.

**Open framing question worth raising explicitly in the meeting:**
- Is "regime detection" meant to be a descriptive/explanatory tool for the memo (current use — human-readable regime calendar) or a predictive input to automated dispatch decisions? Ensemble design differs a lot depending on the answer (confidence-scored descriptive labels vs. a forecastable, probabilistic state for MPC). The current architecture only exercises the descriptive use case.
