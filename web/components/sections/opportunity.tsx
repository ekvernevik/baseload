"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
  ReferenceLine,
} from "recharts";
import valuation from "@/public/data/valuation.json";

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-black border border-white/10 rounded-lg px-4 py-3 text-sm">
      <p className="text-white/60 mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} style={{ color: p.fill || p.color }}>
          {p.name}: <span className="text-white font-medium">{typeof p.value === "number" ? p.value.toFixed(1) : p.value} €/kW/yr</span>
        </p>
      ))}
    </div>
  );
};

export function OpportunitySection() {
  const data = (valuation as any[]).map((d) => ({
    zone: d.zone,
    "Perfect Foresight": +d.pf_eur_per_kw_yr.toFixed(1),
    "Rolling Horizon": +d.rh_eur_per_kw_yr.toFixed(1),
  }));

  const best = (valuation as any[]).reduce((a, b) =>
    a.pf_eur_per_kw_yr > b.pf_eur_per_kw_yr ? a : b
  );

  return (
    <section id="opportunity" className="bg-black py-32 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="mb-16">
          <p className="text-white/40 text-sm uppercase tracking-widest mb-3">03 — The Opportunity</p>
          <h2 className="text-4xl md:text-5xl font-bold text-white tracking-tight mb-4">
            A battery earns real money here.
          </h2>
          <p className="text-white/50 text-lg max-w-2xl">
            Baseload values BESS assets under two dispatch models: a perfect-foresight upper bound
            and a realistic rolling-horizon strategy. The gap between them quantifies forecast value.
            Both show strong, zone-differentiated returns.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-start">
          {/* Valuation chart */}
          <div>
            <h3 className="text-white text-lg font-semibold mb-1">BESS Annual Value by Zone</h3>
            <p className="text-white/40 text-sm mb-6">€/kW/year — perfect foresight vs. rolling horizon dispatch</p>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={data} barGap={6}>
                <XAxis dataKey="zone" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} unit=" €" />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
                <Bar dataKey="Perfect Foresight" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
                <Bar dataKey="Rolling Horizon" fill="#475569" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Headline stats */}
          <div className="flex flex-col gap-6 pt-10">
            <div className="border border-white/10 rounded-xl p-8">
              <p className="text-white/40 text-sm uppercase tracking-widest mb-2">Best Zone</p>
              <p className="text-5xl font-bold text-white mb-1">{best.zone}</p>
              <p className="text-white/50">
                <span className="text-white font-semibold">{best.pf_eur_per_kw_yr.toFixed(0)} €/kW/yr</span> perfect foresight value
              </p>
            </div>

            <div className="border border-white/10 rounded-xl p-8">
              <p className="text-white/40 text-sm uppercase tracking-widest mb-2">Forecast Premium</p>
              {(valuation as any[]).map((d) => {
                const gap = ((d.pf_eur_per_kw_yr - d.rh_eur_per_kw_yr) / d.pf_eur_per_kw_yr * 100);
                return (
                  <div key={d.zone} className="flex justify-between items-center py-2 border-b border-white/5 last:border-0">
                    <span className="text-white/60 text-sm">{d.zone}</span>
                    <span className="text-white text-sm font-medium">{gap.toFixed(1)}% gap</span>
                  </div>
                );
              })}
              <p className="text-white/30 text-xs mt-3">Gap between perfect foresight and rolling horizon</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
