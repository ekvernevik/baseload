# Baseload Phase 1 MVP Memo

## Recommended zones (unconstrained PF)
- NO1: 47.66 €/kW-yr
- NO2: 46.30 €/kW-yr

## Recommended zones (rolling horizon, 24h lookahead)
- NO1: 47.59 €/kW-yr (foresight penalty 0.1%)
- NO2: 46.19 €/kW-yr (foresight penalty 0.2%)

## NTC network discount (unconstrained vs network-constrained)
Measures how much arbitrage value the NTC limits remove from each zone.
Low discount = battery can capture most theoretical value locally.

- NO4: 11.7 → 10.5 €/kW-yr (10% discount)
- NO3: 22.2 → 15.4 €/kW-yr (30% discount)
- NO5: 27.5 → 15.5 €/kW-yr (43% discount)
- NO1: 47.7 → 25.6 €/kW-yr (46% discount)
- NO2: 46.3 → 23.4 €/kW-yr (50% discount)

## Key evidence
- Congestion signal: see `artifacts/figures/spread_heatmap.png`.
- Regime signal: see `artifacts/figures/regime_calendar.png`.
- NTC dispatch: see `artifacts/figures/valuation_pf_ranking.png`.

## Key risks and actions
- volatility_compression: green — Prefer zones with durable spread opportunities; reduce merchant-only exposure.
- tail_dependence: red — Stress-test downside under clipped tails and add downside reserves.
- congestion_persistence: red — Prioritize siting near persistent constrained interfaces.
- oversupply_risk: green — Pair strategy with capture-ready charging and ancillary optionality.
- foresight_risk: green — Invest in short-term forecasting and dispatch tooling before scaling.

## What would change our mind
- Sustained compression in spread/separation metrics for top zones.
- RH penalty remains high after forecast/control improvements.
- Regime mix shifts away from volatility-rich days for multiple months.
- NTC discount narrows significantly (interconnector upgrades or policy change).