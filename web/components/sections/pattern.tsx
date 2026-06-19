"use client";

import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
  Tooltip,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Legend,
} from "recharts";
import regimeProfiles from "@/public/data/regime_profiles.json";

const REGIME_LABELS: Record<number, string> = {
  0: "Low-Price / Calm",
  1: "High-Price / Volatile",
  2: "Congested / Split",
};

const REGIME_COLORS = ["#e2e8f0", "#64748b", "#334155"];

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-black border border-white/10 rounded-lg px-4 py-3 text-sm">
      <p className="text-white/60 mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} style={{ color: p.fill || p.color || "#fff" }}>
          {p.name}: <span className="text-white font-medium">{typeof p.value === "number" ? p.value.toFixed(2) : p.value}</span>
        </p>
      ))}
    </div>
  );
};

export function PatternSection() {
  const profiles = (regimeProfiles as any[]).map((d, i) => ({
    ...d,
    name: REGIME_LABELS[d.cluster] ?? `Regime ${d.cluster}`,
    color: REGIME_COLORS[i] ?? "#888",
  }));

  const radarData = [
    { metric: "Price Level", ...Object.fromEntries(profiles.map((p) => [p.name, +(p.daily_mean_price / 100).toFixed(3)])) },
    { metric: "Volatility", ...Object.fromEntries(profiles.map((p) => [p.name, +(p.daily_iqr / 100).toFixed(3)])) },
    { metric: "Neg. Price", ...Object.fromEntries(profiles.map((p) => [p.name, +(p.neg_price_share * 10).toFixed(3)])) },
    { metric: "Spread P95", ...Object.fromEntries(profiles.map((p) => [p.name, +(p.p95_spread / 100).toFixed(3)])) },
    { metric: "Persistence", ...Object.fromEntries(profiles.map((p) => [p.name, +(p.persistence_proxy).toFixed(3)])) },
  ];

  const barData = profiles.map((p) => ({
    name: p.name,
    "Persistence Score": +p.persistence_proxy.toFixed(3),
    "P95 Spread (scaled)": +(p.p95_spread / 100).toFixed(3),
  }));

  return (
    <section id="pattern" className="bg-neutral-950 py-32 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="mb-16">
          <p className="text-white/40 text-sm uppercase tracking-widest mb-3">02 — The Pattern</p>
          <h2 className="text-4xl md:text-5xl font-bold text-white tracking-tight mb-4">
            Stress isn't random. It clusters.
          </h2>
          <p className="text-white/50 text-lg max-w-2xl">
            Unsupervised clustering on daily market features identifies three distinct grid regimes.
            The congested regime — characterized by high spreads and persistent separation — recurs
            systematically and is not explained by seasonality alone.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          {/* Radar */}
          <div>
            <h3 className="text-white text-lg font-semibold mb-1">Regime Fingerprints</h3>
            <p className="text-white/40 text-sm mb-6">Normalized feature profiles per cluster</p>
            <ResponsiveContainer width="100%" height={320}>
              <RadarChart data={radarData}>
                <PolarGrid stroke="#1e293b" />
                <PolarAngleAxis dataKey="metric" tick={{ fill: "#94a3b8", fontSize: 12 }} />
                <Tooltip content={<CustomTooltip />} />
                {profiles.map((p) => (
                  <Radar
                    key={p.name}
                    name={p.name}
                    dataKey={p.name}
                    stroke={p.color}
                    fill={p.color}
                    fillOpacity={0.15}
                  />
                ))}
                <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          {/* Bar - persistence */}
          <div>
            <h3 className="text-white text-lg font-semibold mb-1">Regime Persistence vs. Spread</h3>
            <p className="text-white/40 text-sm mb-6">How long each regime lasts and how wide it splits the market</p>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={barData} layout="vertical">
                <XAxis type="number" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis dataKey="name" type="category" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} width={140} />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
                <Bar dataKey="Persistence Score" fill="#e2e8f0" radius={[0, 3, 3, 0]} />
                <Bar dataKey="P95 Spread (scaled)" fill="#334155" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Regime stat cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-16 border-t border-white/10 pt-12">
          {profiles.map((p) => (
            <div key={p.name} className="border border-white/10 rounded-xl p-6">
              <div className="w-2 h-2 rounded-full mb-4" style={{ backgroundColor: p.color }} />
              <p className="text-white font-semibold mb-1">{p.name}</p>
              <p className="text-white/40 text-sm">Avg price: <span className="text-white">{p.daily_mean_price.toFixed(0)} €/MWh</span></p>
              <p className="text-white/40 text-sm">P95 spread: <span className="text-white">{p.p95_spread.toFixed(0)} €/MWh</span></p>
              <p className="text-white/40 text-sm">Persistence: <span className="text-white">{(p.persistence_proxy * 100).toFixed(0)}%</span></p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
