import { apiGet } from "./client";

/** Mirrors backend/app/api/health.py HealthResponse. */
export interface HealthCheck {
  status: "ok" | "error";
  detail: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  service: string;
  version: string;
  environment: string;
  checks: Record<string, HealthCheck>;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  // 503 still carries a valid HealthResponse describing what is degraded.
  return apiGet<HealthResponse>("/api/health", { signal, acceptStatuses: [503] });
}
