import { Link, useOutletContext } from "react-router-dom";

import { PreflightPanel } from "../components/PreflightPanel";
import { Panel } from "../components/ui/Panel";
import { StatusBadge, type StatusTone } from "../components/ui/StatusBadge";
import type { BackendHealthState } from "../hooks/useBackendHealth";
import { usePolling } from "../hooks/usePolling";
import { listIncidents, type Incident } from "../lib/api/incidents";

const DONE = new Set(["RESOLVED", "POSTMORTEM_GENERATED", "PREVENTIVE_TASKS_CREATED"]);
const ATTENTION = new Set(["NEEDS_HUMAN", "FAILED", "ROLLED_BACK", "ROLLING_BACK"]);

function stateTone(state: string): StatusTone {
  if (DONE.has(state)) return "success";
  if (ATTENTION.has(state)) return "critical";
  if (state === "AWAITING_APPROVAL") return "warning";
  return "info";
}

function when(value: string) {
  // The API returns UTC timestamps without a zone ("2026-11-07 06:42:50").
  const d = new Date(/[zZ+]/.test(value.slice(10)) ? value : `${value.replace(" ", "T")}Z`);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function IncidentRow({ incident }: { incident: Incident }) {
  return (
    <li className="border-t border-border-subtle py-2 first:border-0 first:pt-0">
      <Link
        to={`/incidents/${incident.incident_id}`}
        className="flex flex-col gap-1 rounded hover:bg-raised"
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold text-fg">{incident.incident_id}</span>
          <span className="text-xs text-fg-muted">
            {incident.service} · {incident.severity ?? "SEV-?"}
          </span>
          <StatusBadge tone={stateTone(incident.state)} label={incident.state} />
        </span>
        <span className="text-sm text-fg-muted">{incident.title}</span>
        <span className="text-xs text-fg-subtle">
          Opened {when(incident.created_at)} · updated {when(incident.updated_at)}
        </span>
      </Link>
    </li>
  );
}

function IncidentsPanel() {
  const state = usePolling(listIncidents, 5_000, "incidents");
  if (state.kind === "loading") return <p className="text-fg-subtle">Loading incidents…</p>;
  if (state.kind === "error") {
    return <p className="text-critical">Could not load incidents: {state.error.message}</p>;
  }
  const incidents = state.data.incidents ?? [];
  if (incidents.length === 0) {
    return (
      <>
        <p className="text-fg-muted">No incidents recorded.</p>
        <p className="mt-1 text-xs text-fg-subtle">
          Incidents appear here when Sentinel opens one from a verified monitoring alert.
        </p>
      </>
    );
  }
  return (
    <ul>
      {incidents.map((incident) => (
        <IncidentRow key={incident.incident_id} incident={incident} />
      ))}
    </ul>
  );
}

function BackendPanel({ state }: { state: BackendHealthState }) {
  if (state.kind === "loading") {
    return <p className="text-fg-subtle">Checking backend health…</p>;
  }
  if (state.kind === "unreachable") {
    return (
      <div className="space-y-1">
        <StatusBadge tone="critical" label="Unreachable" />
        <p className="text-fg-muted">
          {state.error.message}{" "}
          <span className="font-mono text-fg-subtle">({state.error.code})</span>
        </p>
        <p className="text-xs text-fg-subtle">Last attempt {state.checkedAt.toISOString()}</p>
      </div>
    );
  }
  const { health } = state;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <StatusBadge
          tone={health.status === "ok" ? "success" : "warning"}
          label={health.status === "ok" ? "Healthy" : "Degraded"}
        />
        <span className="font-mono text-xs text-fg-muted">
          {health.service} v{health.version} · {health.environment}
        </span>
      </div>
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-fg-subtle">
          <tr>
            <th className="py-1 font-medium">Check</th>
            <th className="py-1 font-medium">Status</th>
            <th className="py-1 font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(health.checks).map(([name, check]) => (
            <tr key={name} className="border-t border-border-subtle">
              <td className="py-1.5 font-mono">{name}</td>
              <td className="py-1.5">
                <StatusBadge
                  tone={check.status === "ok" ? "success" : "critical"}
                  label={check.status}
                />
              </td>
              <td className="py-1.5 font-mono text-xs text-fg-muted">{check.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-fg-subtle">Checked {state.checkedAt.toISOString()}</p>
    </div>
  );
}

export function DashboardPage() {
  const backend = useOutletContext<BackendHealthState>();
  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-2xl font-semibold">Incident Command Dashboard</h1>
      <PreflightPanel />
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Backend">
          <BackendPanel state={backend} />
        </Panel>
        <Panel title="Incidents">
          <IncidentsPanel />
        </Panel>
      </div>
    </div>
  );
}
