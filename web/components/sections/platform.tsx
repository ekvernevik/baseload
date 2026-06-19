"use client";

import { useState } from "react";

const STAGES = [
  {
    id: "ingest_entsoe",
    label: "Ingest ENTSO-E",
    order: "01",
    description:
      "Pulls day-ahead prices, actual generation, and load from the ENTSO-E Transparency Platform for all five Norwegian bidding zones (NO1–NO5). Handles pagination, retry logic, and schema validation.",
  },
  {
    id: "validate_data",
    label: "Validate Data",
    order: "02",
    description:
      "Runs plausibility checks: price bounds, missing-hour detection, cross-zone consistency. Flags anomalies and halts the pipeline if critical quality thresholds are breached.",
  },
  {
    id: "market_metrics",
    label: "Market Metrics",
    order: "03",
    description:
      "Computes zone-level statistics — mean, median, IQR, P95/P99, negative price frequency, and tail ratios. Outputs the distributional fingerprint of each zone.",
  },
  {
    id: "spreads_congestion",
    label: "Spreads & Congestion",
    order: "04",
    description:
      "Calculates pairwise cross-zone spreads for all zone combinations. Identifies congestion hours (spread > threshold), separation frequency, and maximum consecutive congestion runs.",
  },
  {
    id: "congestion_model",
    label: "Congestion Model",
    order: "05",
    description:
      "Attributes congestion events to causal drivers: cold-snap demand, hydro surplus blocked by transmission, or residual. Uses shadow prices and net position data from ENTSO-E.",
  },
  {
    id: "ingest_supplemental",
    label: "Ingest Supplemental",
    order: "06",
    description:
      "Fetches supplemental signals: temperature data for Norwegian zones and reservoir level indices. Used as exogenous features in the regime and congestion models.",
  },
  {
    id: "regime_clustering",
    label: "Regime Clustering",
    order: "07",
    description:
      "Runs k-means clustering on daily market features to identify distinct grid regimes. Labels each day as Low-Price/Calm, High-Price/Volatile, or Congested/Split.",
  },
  {
    id: "bess_valuation_pf",
    label: "BESS Valuation — Perfect Foresight",
    order: "08",
    description:
      "Solves a linear program with full price foresight to compute the theoretical upper bound on BESS annual revenue (€/kW/yr) in each zone. Benchmark for dispatch strategy quality.",
  },
  {
    id: "bess_valuation_rh",
    label: "BESS Valuation — Rolling Horizon",
    order: "09",
    description:
      "Dispatches BESS using a 24-hour rolling horizon — no future price knowledge beyond the window. Measures the real-world achievable fraction of the perfect foresight ceiling.",
  },
  {
    id: "alerts_and_memo",
    label: "Alerts & Memo",
    order: "10",
    description:
      "Generates a structured session report: key metrics, regime summary, BESS valuation table, and any triggered alerts (e.g., anomalous spreads, regime shifts). Saved as markdown.",
  },
];

export function PlatformSection() {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <section id="platform" className="bg-neutral-950 py-32 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="mb-16">
          <p className="text-white/40 text-sm uppercase tracking-widest mb-3">04 — The Platform</p>
          <h2 className="text-4xl md:text-5xl font-bold text-white tracking-tight mb-4">
            End-to-end, fully automated.
          </h2>
          <p className="text-white/50 text-lg max-w-2xl">
            Baseload runs a ten-stage pipeline from raw ENTSO-E data to investment-grade valuation.
            Every artifact is versioned. Every metric is reproducible.
          </p>
        </div>

        <div className="flex flex-col gap-px">
          {STAGES.map((stage) => (
            <div key={stage.id} className="border border-white/10 rounded-xl overflow-hidden">
              <button
                onClick={() => setOpen(open === stage.id ? null : stage.id)}
                className="w-full flex items-center justify-between px-6 py-5 text-left hover:bg-white/5 transition-colors"
              >
                <div className="flex items-center gap-4">
                  <span className="text-white/20 text-xs font-mono">{stage.order}</span>
                  <span className="text-white text-sm font-medium">{stage.label}</span>
                </div>
                <span className="text-white/30 text-lg">{open === stage.id ? "−" : "+"}</span>
              </button>
              {open === stage.id && (
                <div className="px-6 pb-5 border-t border-white/5">
                  <p className="text-white/50 text-sm leading-relaxed pt-4">{stage.description}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
