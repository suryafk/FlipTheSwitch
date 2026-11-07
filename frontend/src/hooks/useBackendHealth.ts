import { useEffect, useState } from "react";

import { ApiError } from "../lib/api/client";
import { getHealth, type HealthResponse } from "../lib/api/health";

export type BackendHealthState =
  | { kind: "loading" }
  | { kind: "ok" | "degraded"; health: HealthResponse; checkedAt: Date }
  | { kind: "unreachable"; error: ApiError; checkedAt: Date };

/** Polls GET /api/health. Every state shown comes from a real response or a real failure. */
export function useBackendHealth(intervalMs = 10_000): BackendHealthState {
  const [state, setState] = useState<BackendHealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let controller = new AbortController();

    const check = async () => {
      controller.abort();
      controller = new AbortController();
      try {
        const health = await getHealth(controller.signal);
        if (!cancelled) setState({ kind: health.status, health, checkedAt: new Date() });
      } catch (err) {
        if (cancelled) return;
        const error =
          err instanceof ApiError ? err : new ApiError(0, "unknown", String(err), null, err);
        setState({ kind: "unreachable", error, checkedAt: new Date() });
      }
    };

    void check();
    const timer = setInterval(() => void check(), intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
      controller.abort();
    };
  }, [intervalMs]);

  return state;
}
