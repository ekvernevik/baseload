# Baseload Phase 1 MVP Memo

## Recommended zones (PF)
- NO1: 0.00 €/kW-yr
- NO2: 0.00 €/kW-yr

## Recommended zones (RH)
- NO1: 47.59 €/kW-yr (penalty 0.1%)
- NO2: 46.19 €/kW-yr (penalty 0.2%)

## Key evidence
- Congestion signal: see `artifacts/figures/spread_heatmap.png`.
- Regime signal: see `artifacts/figures/regime_calendar.png`.

## Key risks and actions
- volatility_compression: green — Prefer zones with durable spread opportunities; reduce merchant-only exposure.
- tail_dependence: green — Stress-test downside under clipped tails and add downside reserves.
- congestion_persistence: green — Prioritize siting near persistent constrained interfaces.
- oversupply_risk: green — Pair strategy with capture-ready charging and ancillary optionality.
- foresight_risk: green — Invest in short-term forecasting and dispatch tooling before scaling.

## What would change our mind
- Sustained compression in spread/separation metrics for top zones.
- RH penalty remains high after forecast/control improvements.
- Regime mix shifts away from volatility-rich days for multiple months.