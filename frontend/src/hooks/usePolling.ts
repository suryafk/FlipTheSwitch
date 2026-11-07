import { useEffect, useState } from "react";

import { ApiError } from "../lib/api/client";

export type PollState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T; checkedAt: Date }
  | { kind: "error"; error: ApiError; checkedAt: Date };

/** Polls an API getter. Every state shown comes from a real response or a real failure. */
export function usePolling<T>(
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  key: string,
): PollState<T> {
  const [state, setState] = useState<PollState<T>>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let controller = new AbortController();
    setState({ kind: "loading" });

    const check = async () => {
      controller.abort();
      controller = new AbortController();
      try {
        const data = await load(controller.signal);
        if (!cancelled) setState({ kind: "ok", data, checkedAt: new Date() });
      } catch (err) {
        if (cancelled) return;
        const error =
          err instanceof ApiError ? err : new ApiError(0, "unknown", String(err), null, err);
        setState({ kind: "error", error, checkedAt: new Date() });
      }
    };

    void check();
    const timer = setInterval(() => void check(), intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
      controller.abort();
    };
    // `load` is expected to be stable per `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, key]);

  return state;
}
