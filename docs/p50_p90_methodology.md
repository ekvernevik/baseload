# BESS P50/P90 Revenue Methodology (issue #21)

Lender-facing definition of how Baseload maps its price-scenario ensemble to
P50/P90 annual merchant revenue for a battery asset. Implemented, end to end
and reproducibly (fixed seeds), by `revenue_percentiles.py`; consumed figures
land in `artifacts/tables/revenue_p50_p90.parquet`.

## 1. Definitions and conventions

- **P50 revenue**: the median of the modelled distribution of annual revenue —
  exceeded in 50 % of modelled years.
- **P90 revenue**: the annual revenue exceeded with **90 % probability**, i.e.
  the **10th percentile** of the distribution. This is the standard lender
  ("1-year P90") convention used for renewables debt sizing; it is *not* the
  90th percentile.
- All figures are nominal EUR, single asset, single zone, energy-arbitrage
  revenue only (day-ahead). Reserve-stack revenue is reported separately by
  `revenue_stack.py` (issue #23) and is not double-counted here.

## 2. Revenue model

The asset is the configured BESS (`bess:` config block: power, energy,
round-trip efficiency, SOC limits). Dispatch inside each scenario is a linear
program maximising arbitrage revenue at the scenario's prices at the
pipeline's native MTU resolution (hourly or 15-min).

## 3. Scenario ensemble and correlation handling

Scenarios come from the probabilistic day-ahead forecast (issue #18):

- A quantile gradient-boosting forecaster is fit on the zone's price history
  *before* the evaluation window (strict out-of-sample split).
- Each scenario adds a bootstrapped sequence of **whole-day residual paths** to
  the P50 forecast. Sampling whole days preserves the intra-day correlation
  structure (morning/evening peak shapes, spike days) that arbitrage revenue
  depends on; day-to-day residual draws are independent, so multi-day
  volatility clustering is only partially represented (see Limitations).
- Cross-zone correlation: percentiles are computed per zone from that zone's
  own ensemble. Portfolio-level aggregation across zones must not sum P90s
  (P90s are not additive); portfolio treatment is out of scope here.

## 4. From scenario dispatch to *realistic* revenue

Scenario dispatch uses perfect foresight *within* the scenario, which
overstates what a causal operator captures. We correct with the **realism
gap** measured by the stochastic-MPC benchmark (issue #19): the shortfall of
causal, scenario-driven CVaR dispatch versus perfect-foresight rolling horizon
over the same window (`valuation_rh.gap_vs_perfect_rh_pct`). When a
stochastic run for the zone exists, its measured gap is used; otherwise the
config default (`p50p90.default_realism_gap_pct`, 15 %) applies. The gap used
is reported per zone in the output table.

## 5. Horizon and annualisation

The evaluation window (`p50p90.eval_days`, default 60 days) is annualised
linearly to 8 760 h. A window that does not cover all seasons under- or
over-states seasonal spread structure — for bankability numbers, run with an
evaluation window spanning a full year of held-out data. Degradation over the
asset's life is layered on separately by the multi-year valuation
(issue #22, `bess_valuation_multiyear.py`), which scales each operating
year's P50/P90 by the degradation-adjusted capacity.

## 6. Documented approximations

1. Scenario dispatch is solved in day-sized LP chunks with an SOC reset at
   0.5·E per chunk — negligible for daily-cycling arbitrage, conservative for
   multi-day strategies.
2. Day-block residual bootstrap underestimates multi-week price regimes; the
   regime layer (issue #17) exists to weight scenarios, and wiring regime
   probabilities into scenario weights is the designated next refinement.
3. The realism gap is a scalar per zone; in reality forecast quality (and
   hence the gap) varies by season.

## 7. Reproducibility

`p50p90.seed` fixes the forecaster fit and every scenario draw; two runs on
the same processed parquet inputs produce identical tables. Provenance of the
inputs is recorded by `validate_data.py` (`data/metadata/provenance.json`).
