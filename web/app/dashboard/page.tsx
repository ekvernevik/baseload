import {
  CongestionPanel,
  PricePanel,
  RegimePanel,
  SpreadPanel,
  ValuationKpis,
  ValuationPanel,
} from "@/components/dashboard/charts";

function SectionLabel({ id, n, title }: { id: string; n: string; title: string }) {
  return (
    <div id={id} className="scroll-mt-20">
      <p className="mb-1 text-xs uppercase tracking-widest text-white/40">
        {n}
      </p>
      <h2 className="mb-5 text-2xl font-bold tracking-tight text-white">
        {title}
      </h2>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-12">
      <div id="overview">
        <h1 className="text-3xl font-bold tracking-tight text-white">
          Nordic BESS overview
        </h1>
        <p className="mt-2 max-w-2xl text-white/50">
          Zone-resolved battery storage economics across NO1–NO5, derived from
          day-ahead prices, cross-zone spreads, and detected market regimes.
        </p>
        <div className="mt-6">
          <ValuationKpis />
        </div>
      </div>

      <section className="flex flex-col gap-5">
        <SectionLabel id="valuation" n="01 — Valuation" title="What a battery is worth, by zone" />
        <ValuationPanel />
      </section>

      <section className="flex flex-col gap-5">
        <SectionLabel id="signal" n="02 — Price & Spread" title="Where the value comes from" />
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <PricePanel />
          <SpreadPanel />
        </div>
      </section>

      <section className="flex flex-col gap-5">
        <SectionLabel id="regimes" n="03 — Regimes" title="Detected market states" />
        <RegimePanel />
      </section>

      <section className="flex flex-col gap-5">
        <SectionLabel id="congestion" n="04 — Congestion" title="Why the grid separates" />
        <CongestionPanel />
      </section>
    </div>
  );
}
