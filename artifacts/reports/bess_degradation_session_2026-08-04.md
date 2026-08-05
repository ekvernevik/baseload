# BESS Degradation & Multi-Year Valuation Session Report
**Date:** 2026-08-04
**Branch:** `feature/bess-degradation-model` (branched from `feature/15-min-mtu-ingest-refactor`)
**Scope:** Norwegian bidding zones NO1–NO5
**Goal:** Replace `bess_valuation_pf.py`'s flat 90% efficiency / no-degradation assumption with a chemistry-aware degradation model (LFP/NMC), feed it into a new multi-year NPV valuation, and add cycling limits so the LP can't dispatch harder than the chemistry would tolerate.

---

## Background

The April 2026 strategic review (`docs/strategic_review_2026-04-19.md`) flagged the BESS model as the weakest link in the valuation pipeline's defensibility: "generic — 90% efficiency, flat throughput cost, no degradation, no cycling limits." Every prior valuation (`valuation_pf.parquet`, `valuation_pf_network.parquet`) is a single-year snapshot computed as if the battery never ages. This overstates long-run value, particularly for lender-facing analysis where the whole point is a defensible multi-year revenue case.

There is no vendor datasheet or TSO/GO-published standard to calibrate against, so this session's degradation parameters are explicitly **placeholder, literature-informed estimates** — directionally correct (LFP degrades slower and cycles harder than NMC; cold weather hurts both) but not warranty-grade. Documenting that clearly, rather than presenting the numbers as authoritative, was the main non-negotiable from the user going in.

---

## What Was Done

### 1. New module: `baseload/bess_degradation.py`

Chemistry presets `LFP` and `NMC` (frozen `ChemistryParams` dataclass), each carrying:
- `roundtrip_efficiency_bol` — beginning-of-life round-trip efficiency
- `calendar_fade_pct_per_yr` / `cycle_fade_pct_per_1000_efc` — linear, additive fade model (deliberately simple — not a rainflow/Arrhenius stress model; see module docstring for why)
- `eol_retention` — capacity fraction defining end-of-life (0.80, industry-standard convention)
- `max_efc_per_day` — warranty-style cycling ceiling
- `cold_derate_temp_c` / `cold_derate_pct_per_c` — one-time ambient-temperature efficiency derate (no seasonal variation modeled)

Functions: `capacity_retention(age_years, cumulative_efc, chem)`, `thermal_efficiency_multiplier(ambient_temp_c, chem)`, `reached_eol(retention, chem)`, `max_efc_for_horizon(chem, horizon_years)`, `get_chemistry(name_or_params)`.

Every number in the module has an inline comment flagging it as a placeholder and explaining the reasoning, per the user's "make it easy, but document it well" instruction.

### 2. `solve_pf()` gained an optional cycling-limit constraint

New `max_cycles: float | None = None` parameter (backward-compatible — existing tests calling it positionally with 7 args are untouched). When set, adds one LP constraint: total throughput ≤ `2 × e_mwh × max_cycles`.

### 3. New `solve_pf_multiyear()` function

Loops year 1..N of asset life:
- computes capacity retention from elapsed age + cumulative cycling (mid-year age convention)
- stops early if retention hits the chemistry's EOL threshold
- re-solves `solve_pf` with the shrunk `e_mwh`, the chemistry's ambient-derated round-trip efficiency, and the cycling-limit constraint
- discounts that year's revenue to present value

Re-uses the same one-year price series for every simulated year (no forward price curve exists) — documented explicitly as a flat-price assumption in the docstring.

### 4. Pipeline wiring

- New `valuation_pf_multiyear` schema + `read/write_valuation_pf_multiyear` io wrappers, registered alongside the existing `valuation_pf`/`valuation_rh` artifacts
- `main()` runs the multi-year pass per zone (unconstrained base only — see Scope Decisions below), writes `artifacts/tables/valuation_pf_multiyear.parquet`, and folds an NPV summary (`npv_eur`, `npv_eur_per_kw`, `effective_life_years`) into the existing `valuation_pf_comparison.parquet`
- New plot `artifacts/figures/valuation_pf_multiyear_degradation.png` — capacity retention and nominal vs. discounted revenue by year, for the top unconstrained zone
- `configs/demo.yaml` gained a `bess.degradation` block: `chemistry: LFP`, `asset_life_years: 15`, `discount_rate: 0.08`, `ambient_temp_c: 6.0`

### 5. Tests: `tests/test_bess_degradation.py` (20 tests)

Covers capacity retention monotonicity/flooring, LFP-vs-NMC contrast, thermal derating (including the extreme-cold floor), end-of-life detection, the cycling-limit constraint actually binding in the LP (and reducing revenue when it does), and `solve_pf_multiyear` behavior (declining retention, discounted ≤ nominal revenue, early EOL termination).

**All 49 tests in the suite pass** (34 in the directly affected files, 49 total).

---

## Scope Decisions

- **Network-constrained (`solve_pf_network`) valuation was not extended to multi-year.** Re-running the PyPSA NTC-constrained solve 15×/zone on top of the existing single annual solve would meaningfully slow the pipeline for a case that wasn't explicitly requested. The multi-year/degradation treatment applies to the unconstrained base valuation only, matching how `bess_valuation_rh.py` already treats PF-unconstrained as its baseline. Flag if network-constrained degradation is wanted later — the same `solve_pf_multiyear` pattern would carry over.
- **`bess_valuation_rh.py` was left untouched.** Its concern (forecast-imperfection penalty) is orthogonal to chemistry degradation; scope was specifically "feed degradation into multi-year valuation," read as the PF path.
- **Chemistry defaults to LFP**, the dominant chemistry for new-build grid-scale storage — configurable per run via `bess.degradation.chemistry`.

---

## Results

Demo config (50 MW / 200 MWh nameplate, NO1–NO5, 2025 prices), LFP chemistry, 15-year requested life, 8% discount rate, 6°C ambient:

| Zone | Unconstrained (€/kW-yr) | NTC-constrained (€/kW-yr) | NTC Discount | NPV (€/kW) | Effective Life (yrs) |
|------|--------------------------|----------------------------|--------------|------------|------------------------|
| NO1  | 47.7 | 25.6 | 46% | 250.8 | 7 |
| NO2  | 46.3 | 23.4 | 50% | 244.4 | 7 |
| NO5  | 27.5 | 15.6 | 43% | 147.2 | 7 |
| NO3  | 22.2 | 15.5 | 30% | 103.0 | 6 |
| NO4  | 11.7 | 10.6 | 10% | 53.8 | 6 |

**Interpretation:**

- Every zone reaches the LFP EOL threshold (80% capacity retention) at **year 6 or 7**, well short of the requested 15-year asset life. At NO1, annual cycling settles around 382–405 equivalent full cycles/year — active arbitrage, but not enough to hit the 547/year LFP cycling ceiling; degradation there is calendar-fade-dominated, not cycling-dominated, under these placeholder parameters.
- This is a materially different picture from the flat, no-degradation valuation: a lender or developer sizing debt on the old `eur_per_kw_yr` figure alone would be assuming a battery that never ages. The NPV column now gives a bounded, discounted view of what the asset actually earns before hitting its capacity floor.
- Because the degradation parameters are placeholders (see Background), the *effective_life_years ≈ 6–7* figure should be read as "this order of magnitude, given typical LFP fade rates" — not as a warranted number. Swapping in real vendor cycle-life/calendar-fade data would directly move this figure.

---

## Artifacts Produced

| Artifact | Location | Description |
|----------|----------|-------------|
| New module | `baseload/bess_degradation.py` | LFP/NMC placeholder chemistry parameters + degradation/derating functions |
| Updated script | `bess_valuation_pf.py` | `solve_pf` cycling-limit param, new `solve_pf_multiyear`, `main()` wiring |
| Schema/io | `baseload/schemas.py`, `baseload/io.py` | `VALUATION_PF_MULTIYEAR_SCHEMA` + read/write wrappers |
| Config | `configs/demo.yaml` | New `bess.degradation` block |
| Test suite | `tests/test_bess_degradation.py` | 20 tests, all passing |
| Multi-year results | `artifacts/tables/valuation_pf_multiyear.parquet` | Per zone-year: retention, effective eta/capacity, cycles, nominal + discounted revenue |
| Updated comparison table | `artifacts/tables/valuation_pf_comparison.parquet` | Now includes `npv_eur`, `npv_eur_per_kw`, `effective_life_years` |
| Degradation plot | `artifacts/figures/valuation_pf_multiyear_degradation.png` | Capacity retention + revenue trajectory, top zone |

---

## Recommended Next Steps

### 1. Replace placeholder chemistry parameters with real data

The single highest-value follow-up. As soon as a vendor datasheet or a TSO/GO-published cycle-life standard is available, every number in `CHEMISTRY_PRESETS` should be updated — the module is structured so this is a pure data swap, no logic changes needed.

**Difficulty:** Low (once a source exists). The blocker is sourcing the data, not the code.

### 2. Extend degradation to the network-constrained valuation

Apply the same `solve_pf_multiyear` pattern to `solve_pf_network`, giving a degraded NTC-constrained NPV alongside the current degraded-unconstrained one. Deferred this session for solve-time reasons (see Scope Decisions).

**Difficulty:** Medium — mainly a runtime/parallelization question (15 years × 5 zones × PyPSA network solve).

### 3. DoD-sensitive cycle fade

The current model treats every equivalent full cycle as equally damaging regardless of depth-of-discharge. A refinement (already possible from the LP's own SOC trace — `soc_s.max() - soc_s.min()`) would weight cycle fade by realized average DoD. Deliberately left out this session per "make it easy" — worth doing once the base placeholder numbers are replaced with real ones, so the added complexity is calibrated against something real.

**Difficulty:** Low-medium.

### 4. Feed the multi-year NPV into `alerts_and_memo.py`

The comparison table now carries `npv_eur_per_kw` and `effective_life_years` per zone — directly relevant to the project-finance framing in the strategic review ("bankability-grade... revenue analysis"). A lender-facing memo section like "NO1's headline 47.7 €/kW-yr assumes an undegraded battery; the asset reaches 80% capacity retention by year 7, giving an NPV of 250.8 €/kW over its LFP-limited life" would connect this work to the actual go-to-market thesis.

**Difficulty:** Low.

### 5. Rolling-horizon degradation

Once network-constrained degradation (#2) exists, the same treatment could apply to `bess_valuation_rh.py`, giving a fully realistic (imperfect-foresight + degraded + NTC-constrained) valuation — the most defensible number the pipeline could produce.

**Difficulty:** Medium-high — compounds the RH script's existing 365 re-solves/zone with the multi-year loop.
