import { apiGet } from "./client";
import type { Incident, TimelineEntry } from "./incidents";

/** Mirrors backend/app/api/warroom.py. Every value comes from a real record. */
export interface MetricsView {
  error_rate_pct: number | null;
  latency_ms_p50: number | null;
  latency_ms_p95: number | null;
  pool_utilization_pct: number | null;
  requests: number | null;
  deploy_id: string | null;
  git_sha: string | null;
  sampled_at: string | null;
}

export interface WarRoom {
  incident: Incident;
  room: { name: string | null; id: string | null };
  stages: { state: string; label: string; status: "done" | "current" | "todo" }[];
  resolved: boolean;
  timeline: TimelineEntry[];
  evidence: { kind: string; label: string; value: string; simulated: boolean }[];
  hypotheses: { statement: string; confidence: number | null }[];
  root_cause: string | null;
  handoffs: {
    handoff_id: string;
    from: string;
    to: string;
    type: string;
    status: string;
    switch_event_id: string | null;
    sha256: string;
    created_at: string;
  }[];
  remediation: {
    remediation_id: string;
    status: string;
    attempts: number;
    commit: string | null;
    base: string;
    patch_hash: string | null;
    coding_mode: string;
    files: string[];
    new_tests: string[];
    new_tests_fail_on_base: boolean | null;
    tests: { passed: number | null; total: number | null; failed: number | null };
    post_check_problems: string[];
    agent: { cost_usd: number | null; duration_s: number | null; summary: string };
    diff: string | null;
  } | null;
  approval: {
    approval_id: string;
    status: string;
    request_id: string;
    action: string | null;
    environment: string | null;
    patch_hash: string | null;
    expires_at: string | null;
    answered_by: string | null;
    answered_at: string | null;
    verification: {
      source: string | null;
      checks: { check: string; ok: boolean; detail: string }[] | null;
    } | null;
  } | null;
  execution: {
    execution_id: string;
    status: string;
    deploy_id: string | null;
    commit: string | null;
    before: MetricsView | null;
    after: MetricsView | null;
    samples: MetricsView[];
  } | null;
  report: { generated_at: string } | null;
  preventive_tasks: { slug: string; text: string; owner: string }[];
}

export function getWarRoom(id: string, signal?: AbortSignal): Promise<WarRoom> {
  return apiGet(`/api/incidents/${encodeURIComponent(id)}/warroom`, { signal });
}

export function getLiveMetrics(signal?: AbortSignal): Promise<MetricsView> {
  return apiGet("/api/metrics/live", { signal, timeoutMs: 4000 });
}
