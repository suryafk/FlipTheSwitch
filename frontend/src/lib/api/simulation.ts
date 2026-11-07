import { apiGet, apiPost } from "./client";

/** Mirrors backend/app/api/simulation.py. */
export interface Scenario {
  id: string;
  title: string;
  kind: "deploy" | "load";
  category: string;
  summary: string;
  breaks: string;
  expected: "fix" | "escalate";
  agents: string;
  multiplier: number | null;
  commit: string | null;
}

export interface SimulationState {
  blocked_reason: string | null;
  baseline: string | null;
  deployed: {
    deploy_id: string | null;
    git_sha: string | null;
    message: string | null;
    on_baseline: boolean;
  };
  metrics: {
    error_rate_pct: number | null;
    latency_ms_p50: number | null;
    pool_utilization_pct: number | null;
    requests: number | null;
  } | null;
  surge: { multiplier: number; since: string } | null;
  active_incident: { incident_id: string; state: string } | null;
  latest_incident: { incident_id: string; state: string } | null;
  scenarios: Scenario[];
}

export function getSimulation(signal?: AbortSignal): Promise<SimulationState> {
  return apiGet("/api/simulation", { signal });
}

export function triggerScenario(id: string): Promise<Record<string, unknown>> {
  return apiPost(`/api/simulation/scenarios/${encodeURIComponent(id)}/trigger`);
}

export function stopSurge(): Promise<{ surge: false; was_running: boolean }> {
  return apiPost("/api/simulation/surge/stop");
}
