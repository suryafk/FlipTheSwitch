import { useParams } from "react-router-dom";

import { Panel } from "../components/ui/Panel";
import { StatusBadge, type StatusTone } from "../components/ui/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { getIncident } from "../lib/api/incidents";

const LEVEL_TONE: Record<string, StatusTone> = {
  info: "info",
  success: "success",
  warning: "warning",
};

/** Target of Slack "View in BLACKBOX" links: incident record and sanitized activity timeline. */
export function IncidentPage() {
  const { incidentId = "" } = useParams();
  const state = usePolling((signal) => getIncident(incidentId, signal), 5_000, incidentId);

  if (state.kind === "loading") return <p className="p-6 text-sm text-fg-muted">Loading…</p>;
  if (state.kind === "error") {
    return (
      <div className="p-6">
        <h1 className="text-lg font-semibold">{incidentId}</h1>
        <p className="text-sm text-critical">
          {state.error.status === 404 ? "No such incident." : state.error.message}
        </p>
      </div>
    );
  }
  const { incident, timeline } = state.data;
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 p-4 md:p-6">
      <h1 className="text-lg font-semibold">
        {incident.incident_id} · {incident.service} · {incident.severity ?? "SEV-?"}
      </h1>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge tone="info" label={incident.state} />
        <span className="text-fg-muted">{incident.title}</span>
      </div>
      <Panel title="Activity">
        {timeline.length === 0 ? (
          <p className="text-sm text-fg-muted">No activity recorded yet.</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {timeline.map((t) => (
              <li key={t.seq} className="flex gap-3 text-sm">
                <time className="w-44 shrink-0 font-mono text-xs text-fg-muted">
                  {new Date(t.at).toLocaleString()}
                </time>
                <StatusBadge tone={LEVEL_TONE[t.level] ?? "neutral"} label={t.agent} />
                <span className="min-w-0 break-words">{t.summary}</span>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </div>
  );
}
