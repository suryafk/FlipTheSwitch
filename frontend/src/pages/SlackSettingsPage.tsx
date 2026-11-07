import type { ReactNode } from "react";

import { Panel } from "../components/ui/Panel";
import { StatusBadge, type StatusTone } from "../components/ui/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { API_BASE_URL } from "../lib/api/client";
import { getSlackStatus, SLACK_INSTALL_PATH, type SlackStatus } from "../lib/api/slack";

const HEALTH: Record<string, { tone: StatusTone; label: string }> = {
  healthy: { tone: "success", label: "Healthy" },
  idle: { tone: "info", label: "Connected, waiting for events" },
  degraded: { tone: "warning", label: "Degraded" },
  not_configured: { tone: "warning", label: "No channel configured" },
  blocked: { tone: "critical", label: "Workspace not allowed" },
  error: { tone: "critical", label: "Error" },
  disconnected: { tone: "neutral", label: "Disconnected" },
};

function when(iso: string | null) {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-4 border-b border-border-subtle py-2 text-sm last:border-0">
      <dt className="w-44 shrink-0 text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg">{children}</dd>
    </div>
  );
}

function Details({ s }: { s: SlackStatus }) {
  const health = HEALTH[s.status] ?? { tone: "neutral" as StatusTone, label: s.status };
  const delivered = s.deliveries.delivered ?? 0;
  const failed = s.deliveries.failed ?? 0;
  const skipped = s.deliveries.skipped ?? 0;
  return (
    <dl>
      <Row label="Health">
        <StatusBadge tone={health.tone} label={health.label} />
      </Row>
      <Row label="Connection">
        {s.connected ? "Connected" : "Not connected"}
        {s.deactivated_reason ? ` (${s.deactivated_reason})` : ""}
      </Row>
      <Row label="Workspace">
        {s.workspace ? `${s.workspace.team_name} (${s.workspace.team_id})` : "none"}
      </Row>
      <Row label="Incident channel">{s.channel_id ?? "not set"}</Row>
      <Row label="Last successful delivery">{when(s.last_success_at)}</Row>
      <Row label="Last error">
        {s.last_error ? `${s.last_error} (${when(s.last_error_at)})` : "none"}
      </Row>
      <Row label="Deliveries">
        {delivered} delivered · {failed} failing · {skipped} skipped · {s.backlog} events not yet
        processed
      </Row>
      <Row label="Bot scopes">{s.scopes.length ? s.scopes.join(", ") : "none"}</Row>
      <Row label="Slash commands">
        {s.commands_enabled ? "/blackbox status, /blackbox help (read-only)" : "disabled"}
      </Row>
    </dl>
  );
}

export function SlackSettingsPage() {
  const state = usePolling(getSlackStatus, 5_000, "slack-status");
  const oauth = state.kind === "ok" && state.data.oauth_configured;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4 md:p-6">
      <h1 className="text-lg font-semibold">Slack integration</h1>
      <p className="text-sm text-fg-muted">
        Slack receives incident notifications only. Agents collaborate in Switch, and remediation
        approvals happen only on the Switch approval card. Nothing in Slack can approve or deploy.
      </p>
      <Panel
        title="Connection"
        actions={
          oauth ? (
            <a
              className="rounded border border-border-subtle px-2 py-1 text-xs text-fg hover:bg-raised"
              href={`${API_BASE_URL}${SLACK_INSTALL_PATH}`}
            >
              {state.kind === "ok" && state.data.connected ? "Reinstall" : "Connect to Slack"}
            </a>
          ) : null
        }
      >
        {state.kind === "loading" && <p className="text-sm text-fg-muted">Loading…</p>}
        {state.kind === "error" && (
          <p className="text-sm text-critical">
            Could not load Slack status: {state.error.message}
          </p>
        )}
        {state.kind === "ok" && <Details s={state.data} />}
        {state.kind === "ok" && !state.data.oauth_configured && (
          <p className="mt-3 text-xs text-fg-muted">
            OAuth is not configured on the server. See docs/11-SLACK-INTEGRATION.md to set up the
            Slack app, or import a bot token with the admin API.
          </p>
        )}
      </Panel>
    </div>
  );
}
