import { apiGet } from "./client";

/** Mirrors backend/app/demo/preflight.py. */
export interface PreflightCheck {
  name: string;
  ok: boolean | null;
  critical: boolean;
  detail: string;
  fix: string;
}

export interface Preflight {
  ready: boolean;
  checked_at: string;
  checks: PreflightCheck[];
}

export function getPreflight(signal?: AbortSignal): Promise<Preflight> {
  return apiGet("/api/preflight", { signal, timeoutMs: 30_000 });
}
