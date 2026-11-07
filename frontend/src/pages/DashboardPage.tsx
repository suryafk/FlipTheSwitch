import { useOutletContext } from "react-router-dom";

import { Panel } from "../components/ui/Panel";
import { StatusBadge } from "../components/ui/StatusBadge";
import type { BackendHealthState } from "../hooks/useBackendHealth";

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
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Backend">
          <BackendPanel state={backend} />
        </Panel>
        <Panel title="Active incidents">
          <p className="text-fg-muted">No incident data yet.</p>
          <p className="mt-1 text-xs text-fg-subtle">
            The incident workflow, Switch integration and agents are not implemented in this phase
            (project foundation). Nothing on this page is simulated.
          </p>
        </Panel>
      </div>
    </div>
  );
}
