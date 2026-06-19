"use client";

import {
  Bar,
  BarChart,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useDataset } from "./use-dataset";

const SLATE = ["#e2e8f0", "#94a3b8", "#64748b", "#475569", "#334155"];

function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-5">
      <h3 className="text-base font-semibold text-white">{title}</h3>
      <p className="mb-5 text-sm text-white/40">{subtitle}</p>
      {children}
    </div>
  );
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-white/10 bg-black px-4 py-3 text-sm">
      <p className="mb-1 text-white/60">{label}</p>
      {payload.map((p: any) => (
        <p key={p.name} style={{ color: p.fill || p.color }}>
          {p.name}:{" "}
          <span className="font-medium text-white">
            {typeof p.value === "number" ? p.value.toFixed(1) : p.value}
          </span>
        </p>
      ))}
    </div>
  );
}

function ChartFrame({
  state,
  height = 280,
  children,
}: {
  state: { loading: boolean; error: string | null; data: unknown };
  height?: number;
  children: React.ReactNode;
}) {
  if (state.loading) {
    return (
      <div
        className="animate-pulse rounded-lg bg-white/5"
        style={{ height }}
        aria-label="Loading"
      />
    );
  }
  if (state.error || !state.data) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-red-400/20 text-sm text-red-400/80"
        style={{ height }}
      >
        Data unavailable
      </div>
    );
  }
  return <ResponsiveContainer width="100%" height={height}>{children as any}</ResponsiveContainer>;
}

const axisTick = { fill: "#94a3b8", fontSize: 12 };
const cursor = { fill: "rgba(255,255,255,0.03)" };

export function ValuationPanel() {
  const state = useDataset("valuation");
  const data = (state.data ?? [])
    .map((d) => ({
      zone: d.zone,
      "Perfect foresight": +d.pf_eur_per_kw_yr.toFixed(1),
      "Rolling horizon": +d.rh_eur_per_kw_yr.toFixed(1),
    }))
    .sort((a, b) => b["Perfect foresight"] - a["Perfect foresight"]);

  return (
    <ChartCard
      title="BESS revenue potential by zone"
      subtitle="Annualised arbitrage value (€/kW/yr) — perfect-foresight vs rolling-horizon"
    >
      <ChartFrame state={state}>
        <BarChart data={data} barGap={4}>
          <XAxis dataKey="zone" tick={axisTick} axisLine={false} tickLine={false} />
          <YAxis tick={axisTick} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} cursor={cursor} />
          <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
          <Bar dataKey="Perfect foresight" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
          <Bar dataKey="Rolling horizon" fill="#475569" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ChartFrame>
    </ChartCard>
  );
}

export function PricePanel() {
  const state = useDataset("zone_stats");
  const data = (state.data ?? []).map((d) => ({
    zone: d.zone,
    "Median": +d.p50.toFixed(1),
    "Mean": +d.mean.toFixed(1),
    "P95": +d.p95.toFixed(1),
  }));

  return (
    <ChartCard
      title="Price distribution by zone"
      subtitle="Median, mean, and P95 day-ahead price (€/MWh)"
    >
      <ChartFrame state={state}>
        <BarChart data={data} barGap={4}>
          <XAxis dataKey="zone" tick={axisTick} axisLine={false} tickLine={false} />
          <YAxis tick={axisTick} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} cursor={cursor} />
          <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
          <Bar dataKey="Median" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
          <Bar dataKey="Mean" fill="#64748b" radius={[3, 3, 0, 0]} />
          <Bar dataKey="P95" fill="#334155" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ChartFrame>
    </ChartCard>
  );
}

export function SpreadPanel() {
  const state = useDataset("spread_metrics");
  const data = (state.data ?? []).map((d) => ({
    pair: d.pair,
    "Mean spread": +d.mean_spread.toFixed(1),
    "P95 spread": +d.p95_abs_spread.toFixed(1),
  }));

  return (
    <ChartCard
      title="Cross-zone spread intensity"
      subtitle="Mean and P95 absolute spread (€/MWh) by zone pair"
    >
      <ChartFrame state={state}>
        <BarChart data={data} barGap={4}>
          <XAxis dataKey="pair" tick={axisTick} axisLine={false} tickLine={false} />
          <YAxis tick={axisTick} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} cursor={cursor} />
          <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
          <Bar dataKey="Mean spread" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
          <Bar dataKey="P95 spread" fill="#334155" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ChartFrame>
    </ChartCard>
  );
}

export function RegimePanel() {
  const state = useDataset("regime_profiles");
  const data = (state.data ?? []).map((d) => ({
    regime: `Regime ${d.cluster}`,
    "Mean price": +d.daily_mean_price.toFixed(1),
    "Daily IQR": +d.daily_iqr.toFixed(1),
    "P95 spread": +d.p95_spread.toFixed(1),
  }));

  return (
    <ChartCard
      title="Market regime profiles"
      subtitle="Cluster centroids — price level, intraday range, and congestion (€/MWh)"
    >
      <ChartFrame state={state}>
        <BarChart data={data} barGap={4}>
          <XAxis dataKey="regime" tick={axisTick} axisLine={false} tickLine={false} />
          <YAxis tick={axisTick} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} cursor={cursor} />
          <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
          <Bar dataKey="Mean price" fill="#e2e8f0" radius={[3, 3, 0, 0]} />
          <Bar dataKey="Daily IQR" fill="#64748b" radius={[3, 3, 0, 0]} />
          <Bar dataKey="P95 spread" fill="#334155" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ChartFrame>
    </ChartCard>
  );
}

export function CongestionPanel() {
  const state = useDataset("congestion_causes");
  // Aggregate counts per zone pair, by cause, into a stacked-friendly shape.
  const byPair = new Map<string, Record<string, number>>();
  for (const row of state.data ?? []) {
    const entry = byPair.get(row.zone_pair) ?? {};
    entry[row.cause] = (entry[row.cause] ?? 0) + row.count;
    byPair.set(row.zone_pair, entry);
  }
  const causes = Array.from(
    new Set((state.data ?? []).map((d) => d.cause)),
  );
  const data = Array.from(byPair.entries()).map(([pair, counts]) => ({
    pair,
    ...counts,
  }));
  const labelFor = (c: string) =>
    c.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());

  return (
    <ChartCard
      title="Congestion attribution"
      subtitle="Constrained hours by driver and zone pair"
    >
      <ChartFrame state={state}>
        <BarChart data={data} barGap={4}>
          <XAxis dataKey="pair" tick={axisTick} axisLine={false} tickLine={false} />
          <YAxis tick={axisTick} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} cursor={cursor} />
          <Legend wrapperStyle={{ color: "#94a3b8", fontSize: 12 }} />
          {causes.map((cause, i) => (
            <Bar key={cause} dataKey={cause} name={labelFor(cause)} stackId="c" fill={SLATE[i % SLATE.length]}>
              {data.map((_, idx) => (
                <Cell key={idx} />
              ))}
            </Bar>
          ))}
        </BarChart>
      </ChartFrame>
    </ChartCard>
  );
}

export function ValuationKpis() {
  const state = useDataset("valuation");
  const rows = [...(state.data ?? [])].sort(
    (a, b) => b.pf_eur_per_kw_yr - a.pf_eur_per_kw_yr,
  );
  const top = rows[0];
  const bottom = rows[rows.length - 1];
  const spread =
    top && bottom ? top.pf_eur_per_kw_yr - bottom.pf_eur_per_kw_yr : 0;

  const kpis = [
    { label: "Best zone", value: top?.zone ?? "—", sub: "highest BESS value" },
    {
      label: "Top revenue",
      value: top ? `€${top.pf_eur_per_kw_yr.toFixed(0)}` : "—",
      sub: "per kW / yr",
    },
    {
      label: "Zone spread",
      value: `€${spread.toFixed(0)}`,
      sub: "best vs worst zone",
    },
    { label: "Zones modelled", value: String(rows.length || 5), sub: "NO1–NO5" },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {kpis.map((k) => (
        <div key={k.label} className="rounded-xl border border-white/10 bg-white/[0.02] p-5">
          <p className="mb-2 text-xs uppercase tracking-widest text-white/40">
            {k.label}
          </p>
          {state.loading ? (
            <div className="h-8 w-20 animate-pulse rounded bg-white/10" />
          ) : (
            <p className="text-3xl font-bold text-white">{k.value}</p>
          )}
          <p className="mt-1 text-sm text-white/40">{k.sub}</p>
        </div>
      ))}
    </div>
  );
}
