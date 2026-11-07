import { useState } from "react";
import { Link } from "react-router-dom";

import { Card } from "../components/warroom/parts";
import { usePolling } from "../hooks/usePolling";
import { ApiError } from "../lib/api/client";
import {
  getSimulation,
  type Scenario,
  type SimulationState,
  stopSurge,
  triggerScenario,
} from "../lib/api/simulation";

function fmt(v: number | null | undefined, unit: string) {
  if (v == null) return "—";
  return unit === "ms" ? `${Math.round(v)} ms` : `${v.toFixed(1)}${unit}`;
}

function StatusCard({
  s,
  onStop,
  busy,
}: {
  s: SimulationState;
  onStop: () => void;
  busy: boolean;
}) {
  const m = s.metrics;
  return (
    <Card title="Sandbox status">
      <div className="grid gap-3 text-sm md:grid-cols-4">
        <div>
          <div className="text-xs text-fg-subtle">Deployed release</div>
          <div className="font-mono">
            #{s.deployed.deploy_id ?? "?"} · {s.deployed.git_sha?.slice(0, 7) ?? "not running"}
          </div>
          <div className={s.deployed.on_baseline ? "text-success" : "text-warning"}>
            {s.deployed.on_baseline ? "healthy baseline (#841)" : "not the baseline"}
          </div>
        </div>
        <div>
          <div className="text-xs text-fg-subtle">Live service (measured)</div>
          <div className="font-mono">
            5xx {fmt(m?.error_rate_pct, "%")} · p50 {fmt(m?.latency_ms_p50, "ms")} · pool{" "}
            {fmt(m?.pool_utilization_pct, "%")}
          </div>
        </div>
        <div>
          <div className="text-xs text-fg-subtle">Incident</div>
          {s.active_incident ? (
            <Link
              to={`/incidents/${s.active_incident.incident_id}`}
              className="font-mono text-accent hover:underline"
            >
              {s.active_incident.incident_id} · {s.active_incident.state} → war room
            </Link>
          ) : s.latest_incident ? (
            <Link
              to={`/incidents/${s.latest_incident.incident_id}`}
              className="font-mono text-fg-muted hover:underline"
            >
              none open (last: {s.latest_incident.incident_id})
            </Link>
          ) : (
            <span className="text-fg-muted">none</span>
          )}
        </div>
        <div>
          <div className="text-xs text-fg-subtle">Traffic</div>
          {s.surge ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-warning">{s.surge.multiplier}× surge running</span>
              <button
                type="button"
                disabled={busy}
                onClick={onStop}
                className="rounded border border-border-strong px-2 py-0.5 text-xs hover:bg-raised disabled:opacity-50"
              >
                End surge
              </button>
            </div>
          ) : (
            <span className="font-mono text-fg-muted">normal (120 rps)</span>
          )}
        </div>
      </div>
      {s.blocked_reason && (
        <p className="mt-3 rounded border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">
          {s.blocked_reason}
        </p>
      )}
    </Card>
  );
}

function ScenarioCard({
  sc,
  blocked,
  onTrigger,
  busy,
}: {
  sc: Scenario;
  blocked: string | null;
  onTrigger: (id: string) => void;
  busy: boolean;
}) {
  const [armed, setArmed] = useState(false);
  const fix = sc.expected === "fix";
  return (
    <section
      className="flex flex-col rounded-md border border-border-subtle bg-panel p-4"
      aria-label={sc.title}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-raised px-1.5 py-0.5 text-[11px] text-fg-muted">
          {sc.category}
        </span>
        <span
          className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${
            fix
              ? "border-success/40 bg-success/10 text-success"
              : "border-warning/40 bg-warning/10 text-warning"
          }`}
        >
          {fix ? "Agents fix it (you approve)" : "Agents escalate to you"}
        </span>
      </div>
      <h2 className="mt-2 text-base font-semibold">{sc.title}</h2>
      <p className="mt-1 text-sm text-fg">{sc.summary}</p>
      <dl className="mt-3 flex flex-col gap-2 text-sm">
        <div>
          <dt className="text-xs font-semibold tracking-wider text-fg-subtle uppercase">
            What breaks
          </dt>
          <dd className="text-fg-muted">{sc.breaks}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold tracking-wider text-fg-subtle uppercase">
            What the agents should do
          </dt>
          <dd className="text-fg-muted">{sc.agents}</dd>
        </div>
      </dl>
      <div className="mt-auto flex items-center justify-between gap-2 pt-4">
        <span className="font-mono text-xs text-fg-subtle">
          {sc.kind === "deploy" ? `deploys commit ${sc.commit ?? "?"}` : "no deploy · load only"}
        </span>
        {armed ? (
          <span className="flex gap-2">
            <button
              type="button"
              onClick={() => setArmed(false)}
              className="rounded border border-border-subtle px-2.5 py-1 text-sm text-fg-muted hover:bg-raised"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={busy || !!blocked}
              onClick={() => {
                setArmed(false);
                onTrigger(sc.id);
              }}
              className="rounded bg-critical px-2.5 py-1 text-sm font-medium text-white hover:bg-critical/90 disabled:opacity-50"
            >
              Break it
            </button>
          </span>
        ) : (
          <button
            type="button"
            disabled={busy || !!blocked}
            title={blocked ?? undefined}
            onClick={() => setArmed(true)}
            className="rounded border border-critical/50 px-2.5 py-1 text-sm text-critical hover:bg-critical/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Trigger
          </button>
        )}
      </div>
    </section>
  );
}

/** Demo control: inject one real fault into the sandbox and let the agents respond. */
export function SimulationPage() {
  const state = usePolling(getSimulation, 3_000, "simulation");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      setNotice({ tone: "ok", text: label });
    } catch (err) {
      setNotice({
        tone: "error",
        text: err instanceof ApiError ? err.message : `Failed: ${String(err)}`,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Error simulation</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Break the sandboxed payment-service in a real way and watch BLACKBOX respond. Each fault
          is a real deploy or a real traffic change; the metrics, logs and diffs the agents use are
          produced by the running service. One incident at a time, starting from the healthy
          baseline.
        </p>
      </div>

      {notice && (
        <p
          role="status"
          className={`rounded border px-3 py-2 text-sm ${
            notice.tone === "ok"
              ? "border-success/40 bg-success/10 text-success"
              : "border-critical/40 bg-critical/10 text-critical"
          }`}
        >
          {notice.text}
        </p>
      )}

      {state.kind === "loading" && <p className="text-sm text-fg-muted">Loading…</p>}
      {state.kind === "error" && (
        <p className="text-sm text-critical">Could not load scenarios: {state.error.message}</p>
      )}
      {state.kind === "ok" && (
        <>
          <StatusCard
            s={state.data}
            busy={busy}
            onStop={() => void run("Surge ended. Traffic returns to 120 rps.", stopSurge)}
          />
          <div className="grid gap-4 md:grid-cols-2">
            {state.data.scenarios.map((sc) => (
              <ScenarioCard
                key={sc.id}
                sc={sc}
                blocked={state.data.blocked_reason}
                busy={busy}
                onTrigger={(id) =>
                  void run(
                    `${sc.title} triggered. Sentinel will open an incident within about 15 s; ` +
                      "it appears above with a link to its war room.",
                    () => triggerScenario(id),
                  )
                }
              />
            ))}
          </div>
          <p className="text-xs text-fg-subtle">
            Reset between runs with <span className="font-mono">make stack-down</span>,{" "}
            <span className="font-mono">make sim-reset</span>,{" "}
            <span className="font-mono">make stack-up</span>. The card processor and the customers
            are simulated; everything else is real.
          </p>
        </>
      )}
    </div>
  );
}
