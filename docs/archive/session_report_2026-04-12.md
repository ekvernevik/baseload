# Baseload — Session Report
**Branch:** Evan's-experimentation  
**Period:** 2026-04-09 to 2026-04-12  
**Base:** main (commit `ec8adda`)

---

## Summary

This branch takes the Phase 1 MVP pipeline from a basic price-arbitrage calculator to a network-aware BESS valuation tool with causal congestion attribution, regime clustering, and a complete investment memo. The core addition is a PyPSA-based NTC-constrained dispatch model that quantifies how much arbitrage value the Norwegian transmission network actually allows a battery to capture — the "NTC discount."

---

## What Was Built

### 1. Expanded Data Ingestion — `ingest_entsoe.py`
- **Rewrote** ingestion from scratch to handle ENTSO-E's actual CSV export formats
- Added `load_load_table()`: parses actual load CSVs, drops forecast columns
- Added `load_actgen_table()`: parses long-format actual generation CSVs with MTU timestamps; filters to relevant Norwegian generation types (Hydro Reservoir, Run-of-river, Pumped Storage, Wind Onshore, Wind Offshore, Fossil Gas, Waste, Other renewable, Solar)
- Added `load_transmission_table()`: parses cross-border flow CSVs; computes net internal NO-NO flows and net external balance per zone (imports from SE, DK, DE, GB, NL, RU)
- Added `_validate_date_range()`: early config validation with clear error messages
- **Result:** Generation deficit hours (>500 MW) dropped from 2,682 → 1,836 after adding non-hydro types. Global system imbalance mean = 15 MW (essentially zero), confirming external balance is complete.

### 2. Congestion Model — `congestion_model.py` (new file)
- Flow-based congestion detection: flags hours where observed flows ≥ 95% of NTC capacity
- Causal attribution: labels congestion events as `hydro_surplus`, `cold_snap`, or `unknown` based on reservoir fill % and temperature (temperature later removed — see below)
- Outputs: `congestion_attribution.parquet`, `congestion_shadow_prices.parquet`

### 3. Supplemental Data Ingestion — `ingest_supplemental.py` (new file)
- Fetches hydro reservoir fill levels from Nord Pool (CSV or API)
- Fetches hydro inflow from NVE HydAPI (by gauging station)
- Temperature intake was built and subsequently removed (see below)
- All outputs optional — congestion model uses whatever is present

### 4. NTC-Constrained BESS Dispatch — `bess_valuation_pf.py`
- Added `solve_pf_network()`: PyPSA StorageUnit LP co-optimized alongside NTC-limited inter-zone flows
- BESS can only charge from local zone surplus, discharge into local zone deficit — physically grounded
- Three PyPSA/linopy implementation quirks resolved and documented:
  1. `marginal_cost_storage` is a SOC standing cost, not a charging cost — charging cost injected via linopy directly
  2. Epsilon degeneracy break (1e-3 €/MWh) prevents simultaneous charge/discharge
  3. Fixed-injection model requires alternating surplus/deficit hours to have dispatch opportunities
- Now runs both formulations per zone and produces:
  - `valuation_pf.parquet` — unconstrained (unchanged, backwards compatible)
  - `valuation_pf_network.parquet` — NTC-constrained
  - `valuation_pf_comparison.parquet` — side-by-side with NTC discount %

### 5. Norway Network Model — `norway_network.py` (renamed from `norway_pypsa_opf.py`)
- 5-zone PyPSA transport model with fixed injections and NTC-limited links
- Renamed because "OPF" was misleading — shadow prices from this model are useless for Norway (hydro marginal cost = water value = circular). The file's real purpose is providing `build_network()` to `bess_valuation_pf.py`
- Dead artifacts removed: `pypsa_shadow_prices.parquet`, `pypsa_lmp.parquet`
- Test file renamed: `test_norway_pypsa_opf.py` → `test_norway_network.py`

### 6. Investment Memo — `alerts_and_memo.py`
- Added NTC discount section to memo output: lists all 5 zones with unconstrained → constrained values and discount %
- Zones sorted by ascending discount (most capturable first)
- Added reference to `valuation_pf_ranking.png` in key evidence
- Updated section headers to clarify PF = unconstrained, RH = rolling horizon

### 7. Documentation Pass — all pipeline scripts
- Replaced filler "restate the code" comments with meaningful "why" context throughout:
  - `bess_valuation_rh.py`: docstrings explaining receding-horizon principle and foresight penalty interpretation
  - `market_metrics.py`: comments explaining IQR vs std choice, tail_ratio definition, negative price frequency significance
  - `regime_clustering.py`: comments explaining daily aggregation rationale, spread features for congestion regime separation, deterministic seed
  - `spreads_congestion.py`: comments explaining 20 €/MWh separation threshold, `max_consecutive_separation_h` as the primary congestion signal
  - `validate_data.py`: comments explaining 6σ outlier threshold (fat tails in Nordic prices), SHA256 provenance checksums
  - `alerts_and_memo.py`: removed pure filler, kept meaningful per-alert context

---

## What Was Removed / Cleaned Up

- **Temperature ingestion removed** from `ingest_supplemental.py` and `configs/demo.yaml`: Open-Meteo fetch, `DEFAULT_CENTROIDS`, `_TEMP_MIN_PLAUSIBLE`/`_TEMP_MAX_PLAUSIBLE`, `cold_snap_threshold_c` config key. Temperature was not adding value to the current pipeline.
- **`norway_pypsa_opf.py` deleted** — replaced by `norway_network.py`
- **`pypsa_shadow_prices.parquet` and `pypsa_lmp.parquet` deleted** — binary outputs with ~0.1 correlation to real spreads, not used downstream

---

## 2025 Results (50 MW / 200 MWh, η=0.9)

| Zone | Unconstrained PF | Network-constrained | NTC Discount | RH (24h) | Foresight Penalty |
|------|-----------------|--------------------:|-------------:|----------:|------------------:|
| NO1  | 47.7 €/kW-yr    | 25.6 €/kW-yr        | 46%          | 47.6      | 0.1%              |
| NO2  | 46.3 €/kW-yr    | 23.4 €/kW-yr        | 50%          | 46.2      | 0.2%              |
| NO5  | 27.5 €/kW-yr    | 15.5 €/kW-yr        | 43%          | —         | —                 |
| NO3  | 22.2 €/kW-yr    | 15.4 €/kW-yr        | 30%          | —         | —                 |
| NO4  | 11.7 €/kW-yr    | 10.5 €/kW-yr        | 10%          | —         | —                 |

Key finding: NO1/NO2 have the highest absolute capturable value (~24-25 €/kW-yr) despite the large NTC discount. NO4 has the most reliable discount profile but a low floor. Foresight penalty is negligible everywhere — 24h price forecasting is sufficient.

---

## Known Limitations / Open Items

- **NTC values are hardcoded** in `norway_network.py`. NO3-NO4 is set at 700 MW; published sources suggest 200-400 MW. Needs verification against Statnett's capacity tables before presenting results.
- **Hydro data not yet wired up** — `ingest_supplemental.py` is ready but NVE API keys and Nord Pool reservoir CSV are not configured. Station IDs are placeholders.
- **Congestion model causal attribution** is partially stubbed — hydro surplus label works once reservoir data is available; cold snap label removed with temperature.
- **No FCR/ancillary services modelling** — arbitrage revenue is the floor of the BESS revenue stack. FCR-N/FCR-D (procured by Statnett) typically 2-5x arbitrage value and are not captured here.

---

## Tests

25 tests passing across:
- `tests/test_norway_network.py` (15 tests) — topology, uncongested routing, congested routing, external balance
- `tests/test_bess_valuation_pf.py` (10 tests) — unconstrained and network-constrained BESS dispatch
