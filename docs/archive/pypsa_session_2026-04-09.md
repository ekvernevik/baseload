# PyPSA Integration Session Report
**Date:** 2026-04-09  
**Scope:** Norwegian bidding zones NO1–NO5  
**Goal:** Produce physically-grounded, continuous shadow prices for NTC interconnectors using PyPSA DC OPF

---

## Background

The existing `congestion_model.py` uses a flow-based approach: when an observed flow exceeds 95% of the NTC limit, it sets the shadow price equal to the observed price spread between the two zones. This is correct and data-grounded, but binary — a line is either congested (price spread) or not (zero).

The goal of this session was to replace or supplement that with a PyPSA DC OPF that produces **continuous** shadow prices from LP dual variables — the marginal value of relaxing each NTC constraint by 1 MW, expressed in real €/MWh. These continuous shadow prices are needed for BESS valuation, where the value of storage depends on how much headroom exists before a constraint binds, not just whether it has already bound.

---

## What We Explored

### 1. PyPSA-Eur Network Topology

We cloned the PyPSA-Eur repository and examined the ENTSO-E grid kit data for Norway. Key findings:

- **169 Norwegian buses, 189 lines** — full OSM-derived grid topology is available
- PyPSA-Eur's `build_bidding_zones.py` explicitly maps buses to NO1–NO5
- A Norway-only OPF (169 buses, hourly, full year) is computationally feasible on a laptop with HiGHS

**Why we didn't use it:** We only have zone-level generation data (`actgen.parquet` aggregated to NO1–NO5). Running a 169-bus OPF with zone-level generation data would require distributing generation across buses with no physical basis. The zone-level 5-bus transport model is the appropriate level of abstraction for our data.

### 2. Data Audit

During network setup, we discovered that `actgen.parquet` was filtering the ENTSO-E A75 generation data to only four types:

- Hydro Water Reservoir
- Hydro Run-of-river and pondage
- Hydro Pumped Storage
- Wind Onshore

Five additional types were present in the raw CSVs but being dropped:

| Type | System-wide mean | Max |
|---|---|---|
| Fossil Gas | ~133 MW | ~242 MW |
| Other renewable | ~66 MW | ~98 MW |
| Waste/MSW | ~30 MW | ~78 MW |
| Solar | ~5 MW | ~14 MW |
| Wind Offshore | ~3 MW | ~5 MW |

**Fix applied:** `_RELEVANT_GEN_TYPES` in `ingest_entsoe.py` was expanded. Deficit hours (global imbalance > 500 MW) dropped from 2,682 to 1,836 — a 32% reduction.

We also verified that the external balance (`external_balance.parquet`) correctly captures all cross-border flows: NO1↔SE3, NO3↔SE2, NO4↔SE1/SE2/FI/RU, NO2↔DK1/GB/NL/DE-LU. All are present in the raw CSVs and correctly netted by `load_transmission_table()`. The global energy balance is essentially correct (mean imbalance = 15 MW across 8,760 hours).

### 3. PyPSA 5-Zone Transport Model

We built `norway_pypsa_opf.py` — a 5-bus zone-level PyPSA network with:

- One bus per bidding zone (NO1–NO5)
- Six bidirectional links with NTC capacity limits
- Fixed observed net injections per zone (actgen + external_balance − load)
- Slack generators at SLACK_COST = 10,000 €/MWh to maintain feasibility
- HiGHS as the LP solver (open source, handles this scale in ~1 second)

Three formulations were attempted before settling on the fixed-injection approach:

| Formulation | Problem |
|---|---|
| Variable dispatch, MC = observed prices | Slack fires in 65% of hours; slack cost (10,000) distorts all LMPs near it |
| Load adjustment (subtract external balance from load) | Made export zones' effective load artificially high; worsened slack usage |
| Fixed net injections (final) | Feasible, but shadow prices still binary (0 or 10,000) |

### 4. What the OPF Actually Produces

With the fixed-injection formulation:

| Interconnector | Congested hours | Mean shadow price |
|---|---|---|
| NO1-NO2 | 3 hrs (0.0%) | n/a |
| NO1-NO3 | 3,354 hrs (38.3%) | 10,000 €/MWh |
| NO1-NO5 | 3,368 hrs (38.4%) | 10,000 €/MWh |
| NO2-NO5 | 3,365 hrs (38.4%) | 10,000 €/MWh |
| NO3-NO4 | 1,000 hrs (11.4%) | 10,000 €/MWh |
| NO3-NO5 | 14 hrs (0.2%) | 10,000 €/MWh |

Correlation with observed price spreads during congested hours: **~0.1** (essentially uncorrelated).

---

## Why It Didn't Work

### The Core Problem: No Real Marginal Cost Structure

LP shadow prices only carry meaning in the units of the objective function. For a transmission shadow price to be in real €/MWh, the objective must be generation cost in real €/MWh.

Norway's generation is ~97% hydro. The marginal cost of hydro is the **water value** — the opportunity cost of releasing water now vs. saving it for future use. Water value is not published, not observable, and is itself derived from market prices. It is effectively circular: the water value equals the expected future electricity price, which is the quantity we're trying to model.

Setting MC = observed price is also circular: when unconstrained, the OPF reproduces the input price trivially; the shadow price adds nothing. When constrained, the shadow price diverges from observed prices in ways that reflect the slack cost, not the real market.

**The result:** Any LP on Norwegian generation produces either circular outputs (MC = prices) or binary outputs (MC = 0 or SLACK_COST). There is no middle ground with our data.

### Why External Data Can't Fix This

| Potential fix | Why it fails |
|---|---|
| Published water values | Not available hourly; proprietary to each producer |
| Add Swedish/Danish thermal MC on cross-border links | Requires a full Nordic model — those generators set prices in their own zones, not Norwegian ones |
| Multi-tier slack generators | Arbitrary tiers don't correspond to real market structure; shadow prices are still model-dependent |
| Full PyPSA-Eur Nordic run | Would work, but requires 64–128 GB RAM and hours of compute; outside scope |

### What Does Work

`congestion_model.py` takes the opposite approach: rather than inferring shadow prices from a model, it reads the shadow price directly from the market outcome. When a line's observed physical flow exceeds 95% of NTC, the market has already priced the congestion into the zone spread. The shadow price = that spread. It is binary (congested/not) but grounded in real €/MWh market data.

---

## What Was Built

| Artifact | Location | Status |
|---|---|---|
| PyPSA 5-zone network | `baseload/norway_pypsa_opf.py` | Working, tested |
| 15 unit tests | `baseload/tests/test_norway_pypsa_opf.py` | All passing |
| Expanded actgen types | `baseload/ingest_entsoe.py` (line 14) | Applied, pipeline re-run |
| Rebuilt actgen | `data/processed/actgen.parquet` | 30 columns (was 20) |

---

## Where This Leaves Us

`norway_pypsa_opf.py` is not useful as a shadow price tool, but the network it builds is the correct foundation for the next step: **BESS dispatch optimization**.

The LP is already set up with:
- Correct zone topology and NTC limits
- Observed generation and load as constraints
- HiGHS solver wired and tested

The missing piece is a PyPSA `StorageUnit` at a zone interface, with observed market prices as the revenue signal. The LP then finds the optimal charge/discharge schedule, respecting NTC constraints. This is a problem PyPSA is well-suited for with our data, and does not require the marginal cost structure that defeated the shadow price attempt.

**Next session:** Integrate PyPSA StorageUnit into `bess_valuation_pf.py`.
