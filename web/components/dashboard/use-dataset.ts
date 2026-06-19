"use client";

import { useEffect, useState } from "react";

import type { DatasetName, DatasetTypeMap } from "@/lib/datasets";

interface State<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

// Client-side fetch against the swappable /api/data endpoint. When Evan repoints
// the data-source seam at live data, this hook (and a future refetch interval)
// is all that's needed to make the dashboard live — no component changes.
export function useDataset<N extends DatasetName>(name: N): State<DatasetTypeMap[N]> {
  const [state, setState] = useState<State<DatasetTypeMap[N]>>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    fetch(`/api/data/${name}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (active) setState({ data, loading: false, error: null });
      })
      .catch((err) => {
        if (active) setState({ data: null, loading: false, error: String(err) });
      });
    return () => {
      active = false;
    };
  }, [name]);

  return state;
}
