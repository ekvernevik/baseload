# Baseload

Grid-centric electricity systems research focused on system constraints, stress conditions, and decision-oriented reasoning. Energy storage serves as a lens to understand broader grid behavior.

## Project Status

**Currently in ideation and system-mapping phase.**

This repository is structured for conceptual development, reasoning, and documentation. Implementation details (languages, tools, frameworks) are intentionally deferred.

## Structure

The project organizes thinking into distinct areas:

- **`framework/`** — Conceptual foundation, terminology, and analytical approach
- **`reasoning/`** — System mapping, constraint analysis, and condition-dependent logic
- **`models/`** — Reserved for simple illustrative examples (not full-scale simulation)
- **`examples/`** — Worked scenarios and case studies
- **`assumptions/`** — Explicit scope boundaries, exclusions, and limitations
- **`references/`** — Background materials, sources, and context

## Principles

- **Make assumptions explicit** — Document what is included, excluded, and simplified
- **Condition-dependent reasoning** — Distinguish normal operation from stressed states
- **Clear over comprehensive** — Emphasize understanding over exhaustive modeling
- **Extensible structure** — Organized to support future collaboration without reorganization

## What This Is Not

This is **not** a full-scale dispatch model, power-flow simulation, or market optimization tool. It is a scaffold for reasoning about grid systems, their constraints, and the role of storage in managing stress conditions.

## How to run the demo

Run from repository root with one config file:

1. `python ingest_entsoe.py --config configs/demo.yaml`
2. `python validate_data.py --config configs/demo.yaml`
3. `python market_metrics.py --config configs/demo.yaml`
4. `python spreads_congestion.py --config configs/demo.yaml`
5. `python regime_clustering.py --config configs/demo.yaml`
6. `python bess_valuation_pf.py --config configs/demo.yaml`
7. `python bess_valuation_rh.py --config configs/demo.yaml`
8. `python alerts_and_memo.py --config configs/demo.yaml`

---

For detailed information about each folder, see the README.md files within them.

## Sources

Transparency Platform - most things
OpenInfraMap - List of Power Plants in Norway
WikiData - Coordinates of said Power Plants