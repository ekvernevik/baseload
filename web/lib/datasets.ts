// Dataset registry + types. Shapes mirror the pipeline's parquet→JSON exports.
// When Evan wires live data, these types stay the contract; only the source
// (see lib/data-source.ts) changes.

export const DATASETS = [
  "zone_stats",
  "spread_metrics",
  "regime_profiles",
  "valuation",
  "congestion_causes",
] as const;

export type DatasetName = (typeof DATASETS)[number];

export function isDatasetName(value: string): value is DatasetName {
  return (DATASETS as readonly string[]).includes(value);
}

export interface ZoneStat {
  zone: string;
  mean: number;
  std: number;
  iqr: number;
  p5: number;
  p50: number;
  p95: number;
  p99: number;
  negative_price_freq: number;
  tail_ratio: number;
}

export interface SpreadMetric {
  pair: string;
  mean_spread: number;
  std_spread: number;
  p95_abs_spread: number;
  separation_freq: number;
  max_consecutive_separation_h: number;
}

export interface RegimeProfile {
  cluster: number;
  daily_mean_price: number;
  daily_iqr: number;
  neg_price_share: number;
  p95_spread: number;
  persistence_proxy: number;
}

export interface Valuation {
  zone: string;
  pf_eur_per_kw_yr: number;
  rh_eur_per_kw_yr: number;
}

export interface CongestionCause {
  zone_pair: string;
  cause: string;
  count: number;
}

export interface DatasetTypeMap {
  zone_stats: ZoneStat[];
  spread_metrics: SpreadMetric[];
  regime_profiles: RegimeProfile[];
  valuation: Valuation[];
  congestion_causes: CongestionCause[];
}
