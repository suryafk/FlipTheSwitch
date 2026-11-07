import { apiGet } from "./client";

/** Mirrors backend/app/api/incidents.py. */
export interface Incident {
  incident_id: string;
  service: string;
  severity: string | null;
  state: string;
  title: string;
  room_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineEntry {
  seq: number;
  at: string;
  agent: string;
  summary: string;
  level: "info" | "success" | "warning";
  kind: string;
}

export function getIncident(
  id: string,
  signal?: AbortSignal,
): Promise<{ incident: Incident; timeline: TimelineEntry[] }> {
  return apiGet(`/api/incidents/${encodeURIComponent(id)}`, { signal });
}

export function listIncidents(signal?: AbortSignal): Promise<{ incidents: Incident[] }> {
  return apiGet("/api/incidents", { signal });
}
