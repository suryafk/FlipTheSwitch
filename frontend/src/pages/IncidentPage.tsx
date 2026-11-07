import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import {
  ApprovalPanel,
  Card,
  Feed,
  MetricChart,
  PatchPanel,
  RecoveryPanel,
  RootCausePanel,
  StageStrip,
} from "../components/warroom/parts";
import { StatusBadge, type StatusTone } from "../components/ui/StatusBadge";
import { useLiveSeries } from "../hooks/useLiveSeries";
import { usePolling } from "../hooks/usePolling";
import { getWarRoom } from "../lib/api/warroom";
import { duration, parseUtc } from "../lib/time";

function stateTone(state: string, resolved: boolean): StatusTone {
  if (resolved) return "success";
  if (["NEEDS_HUMAN", "FAILED", "ROLLED_BACK", "ROLLING_BACK"].includes(state)) return "critical";
  if (state === "AWAITING_APPROVAL") return "warning";
  return "info";
}

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** The war room: one incident, live. Target of the Slack "View in BLACKBOX" links. */
export function IncidentPage() {
  const { incidentId = "" } = useParams();
  const state = usePolling((signal) => getWarRoom(incidentId, signal), 2_000, incidentId);
  const { points, error: metricsError } = useLiveSeries();
  const now = useNow();

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

  const w = state.data;
  const inc = w.incident;
  const opened = parseUtc(inc.created_at);
  const closed = w.resolved ? parseUtc(inc.updated_at) : null;
  const elapsed = opened ? (closed ?? new Date(now)).getTime() - opened.getTime() : 0;

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="text-xl font-semibold">
          {inc.incident_id}{" "}
          <span className="font-normal text-fg-muted">
            · {inc.service} · {inc.severity ?? "SEV-?"}
          </span>
        </h1>
        <StatusBadge tone={stateTone(inc.state, w.resolved)} label={inc.state} />
        <span className="font-mono text-sm text-fg-muted" aria-label="Time since detection">
          ⏱ {duration(elapsed)}
          {w.resolved ? " to resolution" : ""}
        </span>
        {w.room.name && (
          <span className="text-sm text-fg-muted">
            Switch room <span className="font-mono text-fg">#{w.room.name}</span>
          </span>
        )}
      </header>
      <p className="-mt-2 text-sm text-fg-muted">{inc.title}</p>

      <StageStrip stages={w.stages} />

      <section className="grid gap-3 md:grid-cols-3" aria-label="Live service metrics">
        <MetricChart
          label="5xx rate"
          metric="error_rate_pct"
          unit="%"
          threshold={5}
          points={points}
        />
        <MetricChart
          label="p50 latency"
          metric="latency_ms_p50"
          unit="ms"
          threshold={1000}
          points={points}
        />
        <MetricChart
          label="DB pool"
          metric="pool_utilization_pct"
          unit="%"
          threshold={90}
          points={points}
        />
      </section>
      {metricsError && points.length === 0 && (
        <p className="-mt-2 text-xs text-fg-subtle">
          Live metrics unavailable ({metricsError}); start the stack to see them.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card
          title="Agent activity"
          right={<span className="text-xs text-fg-subtle">{w.timeline.length} events</span>}
        >
          <Feed timeline={w.timeline} handoffs={w.handoffs} />
        </Card>
        <div className="flex min-w-0 flex-col gap-4">
          <ApprovalPanel w={w} />
          <RecoveryPanel w={w} />
          <RootCausePanel w={w} />
          <PatchPanel w={w} />
        </div>
      </div>
    </div>
  );
}
