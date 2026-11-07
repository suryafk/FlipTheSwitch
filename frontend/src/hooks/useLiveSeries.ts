import { useEffect, useState } from "react";

import { getLiveMetrics, type MetricsView } from "../lib/api/warroom";

export interface SeriesPoint {
  t: number;
  m: MetricsView;
}

/** Polls the measured service window and keeps a rolling series (nothing is synthesized). */
export function useLiveSeries(intervalMs = 2000, keep = 90) {
  const [points, setPoints] = useState<SeriesPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const tick = async () => {
      try {
        const m = await getLiveMetrics(controller.signal);
        if (cancelled) return;
        setError(null);
        setPoints((prev) => [...prev, { t: Date.now(), m }].slice(-keep));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
      controller.abort();
    };
  }, [intervalMs, keep]);

  return { points, error };
}
