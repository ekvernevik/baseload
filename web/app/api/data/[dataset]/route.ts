import type { NextRequest } from "next/server";

import { hasValidSession } from "@/lib/auth";
import { getDataset } from "@/lib/data-source";
import { isDatasetName } from "@/lib/datasets";

// Swappable data endpoint backing the dashboard. Delegates to the lib/data-source
// seam, so repointing at live data is a one-file change there. Gated by session.
export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ dataset: string }> },
) {
  if (!(await hasValidSession())) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const { dataset } = await ctx.params;
  if (!isDatasetName(dataset)) {
    return Response.json({ error: "unknown dataset" }, { status: 404 });
  }

  try {
    const data = await getDataset(dataset);
    return Response.json(data);
  } catch {
    return Response.json({ error: "dataset unavailable" }, { status: 502 });
  }
}
