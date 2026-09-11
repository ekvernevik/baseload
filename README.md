# Baseload

Can public Nordic grid data forecast load extremes and congestion better than the official forecasts on ENTSO-E — and explain where, when and why congestion happens?

Baseload is a portfolio research project covering the Nordic flow-based bidding zones (NO1–NO5, SE1–SE4, FI, DK1, DK2). Full plan, scope and decisions: **[docs/PROJECT_MAP.md](docs/PROJECT_MAP.md)**.

## The question

ENTSO-E publishes TSO load forecasts as min/max envelopes at week-, month- and year-ahead horizons. For NO1, actual load repeatedly lands on or outside those envelopes — especially below the forecast minimum. Baseload investigates this in three linked studies:

| Study | Question |
|---|---|
| **0 — Data audit** | Are the misses real forecasting errors, or artefacts of resolution, definitions, revisions or data quality? |
| **1 — Load envelopes** | Can a model built only on public data produce better-calibrated min/max load forecasts than ENTSO-E? |
| **2 — Congestion** | Where, when and why does congestion occur in the flow-based market — and do corrected load envelopes help forecast it? |

## Outputs

- Forecast-vs-ENTSO-E charts: both forecast bands against actual min/max load, per zone and horizon
- Congestion analysis: which network elements bind, when, and under which system conditions
- A written report tying the studies together

## Status

Early transition. The repository still contains code from an earlier BESS valuation framing, which is being removed (see [project map §7](docs/PROJECT_MAP.md#7-repository-transition)). The current roadmap starts with that cleanup, then ingesting ENTSO-E load forecasts for all Nordic zones.

## Running what exists today

Requires Python 3 and `pip install -r requirements.txt`. From the repository root:

1. `python ingest_entsoe.py --config configs/demo.yaml`
2. `python validate_data.py --config configs/demo.yaml`
3. `python market_metrics.py --config configs/demo.yaml`
4. `python spreads_congestion.py --config configs/demo.yaml`

`fetch_entsoe_api.py` pulls data directly from the ENTSO-E API (requires an API key).

## Sources

- [ENTSO-E Transparency Platform](https://transparency.entsoe.eu) — load, load forecasts, prices, flows, generation
- JAO / Nordic RCC — flow-based market coupling results (planned)
- NVE and Nord Pool — hydro reservoir and inflow data
- OpenInfraMap and Wikidata — power plant locations in Norway
