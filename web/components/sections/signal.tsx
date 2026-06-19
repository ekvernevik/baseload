"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
  Cell,
} from "recharts";
import zoneStats from "@/public/data/zone_stats.json";
import spreadMetrics from "@/public/data/spread_metrics.json";

const ZONE_COLORS: Record<string, string> = {
  NO1: "#e2e8f0",
  NO2: "#94a3b8",
  NO3: "#475569",
  NO4: "#1e293b",
  NO5: "#64748b",
};

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-black border border-white/10 rounded-lg px-4 py-3 text-sm">
      <p className="text-white/60 mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} style={{ color: p.fill || p.color }}>
          {p.name}: <span className="text-white font-medium">{typeof p.value === "number" ? p.value.toFixed(1) : p.value}</span>
        </p>
      ))}
    </div>
  );
};

export function SignalSection() {
  const priceData = (zoneStats as any[]).map((d) => ({
    zone: d.zone,
    "Median (€/MWh)": +d.p50.toFixed(1),
    "P95 (€/MWh)": +d.p95.toFixed(1),
    "Mean (€/MWh)": +d.mean.toFixed(1),
  }));

  const spreadData = (spreadMetrics as any[]).map((d) => ({
    pair: d.pair,
    "Mean Spread": +d.mean_spread.toFixed(1),
    "P95 Spread": +d.p95_abs_spread.toFixed(1),
    "Separation Freq %": +(d.separation_freq * 100).toFixed(1),
  }));

  return (
    <section id="signal" className="bg-black py-32 px-6">
      <div className="max-w-6xl mx-auto">
        <div className="mb-16">
          <p className="text-white/40 text-sm uppercase tracking-widest mb-3">01 — The Signal</p>
          <h2 className="text-4xl md:text-5xl font-bold text-white tracking-tight mb-4">
            The grid is not one market.
          </h2>
          <p className="text-white/50 text-lg max-w-2xl">
            Norway's five bidding zones price independently. When transmission constraints bind,
            spreads between zones can exceed €90/MWh — creating systematic, recurring arbitrage windows.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
          {/* Price by Zone */}
          <div>
            <h3 className="text-white text-lg font-semibold mb-1">Price Distribution by Zone</h3>
            <p className="text-white/40 text-sm mb-6">Median, mean, and P95 day-ahead prices (€/MWh)</p>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={priceData} barGap={4}>
                <XAxis dataKey="zone" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
                <Bar dataKey="Median (€/MWh)" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
                <Bar dataKey="Mean (€/MWh)" fill="#64748b" radius={[3, 3, 0, 0]} />
                <Bar dataKey="P95 (€/MWh)" fill="#334155" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Spread by Pair */}
          <div>
            <h3 className="text-white text-lg font-semibold mb-1">Cross-Zone Spread Intensity</h3>
            <p className="text-white/40 text-sm mb-6">Mean and P95 absolute spread (€/MWh) by zone pair</p>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={spreadData} barGap={4}>
                <XAxis dataKey="pair" tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
                <Bar dataKey="Mean Spread" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
                <Bar dataKey="P95 Spread" fill="#334155" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Stat callouts */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mt-16 border-t border-white/10 pt-12">
          {(spreadMetrics as any[]).slice(0, 4).map((d) => (
            <div key={d.pair}>
              <p className="text-white/40 text-xs uppercase tracking-widest mb-1">{d.pair}</p>
              <p className="text-white text-2xl font-bold">{(d.separation_freq * 100).toFixed(0)}%</p>
              <p className="text-white/40 text-sm">of hours separated</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
