import { promises as fs } from "node:fs";
import path from "node:path";

import type { DatasetName, DatasetTypeMap } from "./datasets";

// ─────────────────────────────────────────────────────────────────────────────
// SWAP POINT — this is the single seam between the dashboard and its data.
//
// Today: reads the pipeline's exported JSON snapshots from public/data at
// request time (so file refreshes show up without a rebuild).
//
// Later (Evan's live-data task): replace the body of `getDataset` with a call to
// the live source — the pipeline's HTTP export, an object store, or a DB query —
// keeping the same (name) -> DatasetTypeMap[name] contract. Nothing else in the
// dashboard needs to change.
// ─────────────────────────────────────────────────────────────────────────────

const DATA_DIR = path.join(process.cwd(), "public", "data");

export async function getDataset<N extends DatasetName>(
  name: N,
): Promise<DatasetTypeMap[N]> {
  const file = path.join(DATA_DIR, `${name}.json`);
  const raw = await fs.readFile(file, "utf8");
  return JSON.parse(raw) as DatasetTypeMap[N];
}
