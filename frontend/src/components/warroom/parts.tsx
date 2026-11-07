import type { ReactNode } from "react";

import type { SeriesPoint } from "../../hooks/useLiveSeries";
import type { TimelineEntry } from "../../lib/api/incidents";
import type { MetricsView, WarRoom } from "../../lib/api/warroom";
import { clock } from "../../lib/time";

const AGENT_STYLE: Record<string, string> = {
  Sentinel: "text-agent-sentinel border-agent-sentinel/40 bg-agent-sentinel/10",
  RootCause: "text-agent-rootcause border-agent-rootcause/40 bg-agent-rootcause/10",
  Recovery: "text-agent-recovery border-agent-recovery/40 bg-agent-recovery/10",
  Executor: "text-warning border-warning/40 bg-warning/10",
  Control: "text-agent-control border-agent-control/40 bg-agent-control/10",
};

const AGENT_KEY: Record<string, string> = {
  sentinel: "Sentinel",
  rootcause: "RootCause",
  recovery: "Recovery",
  executor: "Executor",
  control: "Control",
};

export function AgentChip({ name }: { name: string }) {
  const label = AGENT_KEY[name] ?? name;
  return (
    <span
      className={`inline-flex h-5 shrink-0 items-center rounded border px-1.5 text-xs font-medium ${
        AGENT_STYLE[label] ?? "border-border-subtle text-fg-muted"
      }`}
    >
      {label}
    </span>
  );
}

export function Card({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-md border border-border-subtle bg-panel" aria-label={title}>
      <header className="flex h-9 items-center justify-between border-b border-border-subtle px-3">
        <h2 className="text-xs font-semibold tracking-wider text-fg-muted uppercase">{title}</h2>
        {right}
      </header>
      <div className="p-3">{children}</div>
    </section>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-fg-subtle">{children}</p>;
}

// ---------- stage strip ----------

export function StageStrip({ stages }: { stages: WarRoom["stages"] }) {
  return (
    <ol className="flex flex-wrap gap-1" aria-label="Incident progress">
      {stages.map((s) => (
        <li
          key={s.state}
          aria-current={s.status === "current" ? "step" : undefined}
          className={`flex h-7 items-center gap-1.5 rounded px-2.5 text-xs font-medium ${
            s.status === "done"
              ? "bg-success/15 text-success"
              : s.status === "current"
                ? "bg-accent/20 text-fg ring-1 ring-accent"
                : "bg-raised text-fg-subtle"
          }`}
        >
          <span aria-hidden="true">
            {s.status === "done" ? "✓" : s.status === "current" ? "●" : "○"}
          </span>
          {s.label}
        </li>
      ))}
    </ol>
  );
}

// ---------- live metric chart (measured values only) ----------

type MetricKey = "error_rate_pct" | "latency_ms_p50" | "pool_utilization_pct";

export function MetricChart({
  label,
  metric,
  unit,
  threshold,
  points,
}: {
  label: string;
  metric: MetricKey;
  unit: string;
  threshold: number;
  points: SeriesPoint[];
}) {
  const values = points.map((p) => p.m[metric]).filter((v): v is number => v != null);
  const current = values.at(-1);
  const max = Math.max(threshold * 1.4, ...values, 1);
  const w = 300;
  const h = 70;
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const path = values
    .map(
      (v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`,
    )
    .join(" ");
  const bad = current != null && current >= threshold;
  const ty = h - (threshold / max) * h;
  const fmt = (v: number) => (unit === "ms" ? Math.round(v).toString() : v.toFixed(1));
  return (
    <div className="rounded-md border border-border-subtle bg-panel p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-semibold tracking-wider text-fg-muted uppercase">
          {label}
        </span>
        <span
          className={`font-mono text-2xl font-semibold ${bad ? "text-critical" : "text-success"}`}
        >
          {current == null ? "—" : fmt(current)}
          <span className="ml-0.5 text-sm text-fg-muted">{unit}</span>
        </span>
      </div>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="mt-2 h-16 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${label} over the last ${values.length} samples`}
      >
        <line
          x1="0"
          x2={w}
          y1={ty}
          y2={ty}
          stroke="var(--color-warning)"
          strokeDasharray="4 4"
          strokeWidth="1"
          opacity="0.6"
        />
        {path && (
          <path
            d={path}
            fill="none"
            stroke={bad ? "var(--color-critical)" : "var(--color-success)"}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      <p className="mt-1 text-[11px] text-fg-subtle">
        alert threshold {fmt(threshold)}
        {unit} · measured by payment-service
      </p>
    </div>
  );
}

// ---------- activity feed ----------

const LEVEL_DOT: Record<string, string> = {
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
};

export function Feed({
  timeline,
  handoffs,
}: {
  timeline: TimelineEntry[];
  handoffs: WarRoom["handoffs"];
}) {
  if (timeline.length === 0) return <Muted>No activity yet.</Muted>;
  const byId = new Map(handoffs.map((h) => [h.handoff_id, h]));
  return (
    <ol className="flex flex-col gap-2.5">
      {timeline.map((t) => {
        const hid = /HND-\d+-\d+/.exec(t.summary)?.[0];
        const handoff = t.kind === "handoff.sent" && hid ? byId.get(hid) : undefined;
        return (
          <li key={t.seq} className="flex gap-3 text-sm">
            <time className="w-16 shrink-0 pt-0.5 font-mono text-xs text-fg-subtle">
              {clock(t.at)}
            </time>
            <span
              aria-hidden="true"
              className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${LEVEL_DOT[t.level] ?? "bg-neutral"}`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-start gap-2">
                <AgentChip name={t.agent} />
                <span className="min-w-0 break-words text-fg">{t.summary}</span>
              </div>
              {handoff && (
                <div className="mt-1.5 rounded border border-border-subtle bg-inset px-2.5 py-1.5 font-mono text-xs text-fg-muted">
                  {handoff.handoff_id} · {handoff.from} → {handoff.to} · {handoff.type} ·{" "}
                  <span className={handoff.status === "acked" ? "text-success" : "text-warning"}>
                    {handoff.status === "acked" ? "ACK ✓" : handoff.status}
                  </span>
                  <br />
                  sha256 {handoff.sha256.slice(0, 16)}… · Switch event{" "}
                  {handoff.switch_event_id ?? "—"}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// ---------- panels ----------

export function RootCausePanel({ w }: { w: WarRoom }) {
  return (
    <Card title="Root cause & evidence">
      {w.root_cause ? (
        <p className="text-sm text-fg">{w.root_cause}</p>
      ) : (
        <Muted>Investigating…</Muted>
      )}
      {w.evidence.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 text-xs">
          {w.evidence.map((e, i) => (
            <li key={i} className="flex gap-2 border-t border-border-subtle pt-1">
              <span className="w-24 shrink-0 font-mono text-fg-subtle">{e.kind}</span>
              <span className="min-w-0 flex-1 break-words text-fg-muted">{e.label}</span>
              <span className="max-w-[45%] text-right font-mono break-words text-fg">
                {e.value}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="mt-2 max-h-64 overflow-auto rounded bg-inset p-2 font-mono text-[11px] leading-4">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+") && !line.startsWith("+++")
              ? "text-success"
              : line.startsWith("-") && !line.startsWith("---")
                ? "text-critical"
                : line.startsWith("@@")
                  ? "text-info"
                  : "text-fg-muted"
          }
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

export function PatchPanel({ w }: { w: WarRoom }) {
  const r = w.remediation;
  return (
    <Card
      title="Patch & tests"
      right={
        r && (
          <span className="text-xs text-fg-muted">
            {r.coding_mode === "claude_code" ? "Claude Code" : "fallback [SIMULATED]"}
          </span>
        )
      }
    >
      {!r ? (
        <Muted>No patch yet.</Muted>
      ) : (
        <>
          <dl className="grid grid-cols-[6rem_1fr] gap-x-2 gap-y-1 text-xs">
            <dt className="text-fg-subtle">Commit</dt>
            <dd className="font-mono">{r.commit?.slice(0, 12) ?? "—"}</dd>
            <dt className="text-fg-subtle">Patch hash</dt>
            <dd className="font-mono break-all">{r.patch_hash ?? "—"}</dd>
            <dt className="text-fg-subtle">Files</dt>
            <dd className="font-mono">{r.files.join(", ")}</dd>
            <dt className="text-fg-subtle">Tests</dt>
            <dd className={r.tests.failed ? "text-critical" : "text-success"}>
              {r.tests.passed}/{r.tests.total} passed
              {r.new_tests.length > 0 &&
                ` · new: ${r.new_tests.join(", ")}${
                  r.new_tests_fail_on_base ? " (fails on the bad commit ✓)" : ""
                }`}
            </dd>
          </dl>
          {r.diff && <DiffView diff={r.diff} />}
        </>
      )}
    </Card>
  );
}

export function ApprovalPanel({ w }: { w: WarRoom }) {
  const a = w.approval;
  const tone =
    a?.status === "approved"
      ? "text-success"
      : a?.status === "rejected" || a?.status === "revoked" || a?.status === "expired"
        ? "text-critical"
        : "text-warning";
  return (
    <Card title="Human approval (Switch)">
      {!a ? (
        <Muted>Not requested yet.</Muted>
      ) : (
        <>
          <p className={`text-sm font-semibold ${tone}`}>
            {a.status === "open" || a.status === "bound"
              ? "Waiting for the incident commander on the Switch card"
              : a.status === "approved"
                ? `Approved by ${a.answered_by ?? "?"} at ${clock(a.answered_at)}`
                : `${a.status.charAt(0).toUpperCase()}${a.status.slice(1)}`}
          </p>
          <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-x-2 gap-y-1 text-xs">
            <dt className="text-fg-subtle">Action</dt>
            <dd className="font-mono">{a.action}</dd>
            <dt className="text-fg-subtle">Target</dt>
            <dd className="font-mono">{a.environment}</dd>
            <dt className="text-fg-subtle">Patch hash</dt>
            <dd className="font-mono break-all">{a.patch_hash}</dd>
            <dt className="text-fg-subtle">Expires</dt>
            <dd className="font-mono">{clock(a.expires_at)}</dd>
          </dl>
          {a.verification?.checks && (
            <ul className="mt-2 grid grid-cols-1 gap-0.5 text-xs sm:grid-cols-2">
              {a.verification.checks.map((c) => (
                <li key={c.check} className={c.ok ? "text-success" : "text-critical"}>
                  {c.ok ? "✓" : "✗"} {c.check.replaceAll("_", " ")}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11px] text-fg-subtle">
            Only the Switch card can approve. The executor re-reads the decision from Switch before
            deploying.
          </p>
        </>
      )}
    </Card>
  );
}

function Delta({
  label,
  before,
  after,
  unit,
}: {
  label: string;
  before: number | null | undefined;
  after: number | null | undefined;
  unit: string;
}) {
  const fmt = (v: number | null | undefined) =>
    v == null ? "—" : unit === "ms" ? Math.round(v) : v.toFixed(2);
  return (
    <div className="rounded border border-border-subtle bg-inset p-2">
      <div className="text-[11px] tracking-wider text-fg-subtle uppercase">{label}</div>
      <div className="font-mono text-sm">
        <span className="text-critical">{fmt(before)}</span>
        <span className="text-fg-subtle"> → </span>
        <span className="text-success">{fmt(after)}</span>
        <span className="text-fg-subtle"> {unit}</span>
      </div>
    </div>
  );
}

export function RecoveryPanel({ w }: { w: WarRoom }) {
  const x = w.execution;
  const b: MetricsView | null | undefined = x?.before;
  const a: MetricsView | null | undefined = x?.after;
  return (
    <Card
      title="Deploy & recovery"
      right={x && <span className="text-xs text-fg-muted">deploy #{x.deploy_id}</span>}
    >
      {!x ? (
        <Muted>Nothing deployed. Deploys need a verified Switch approval.</Muted>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Delta label="5xx" before={b?.error_rate_pct} after={a?.error_rate_pct} unit="%" />
            <Delta label="p50" before={b?.latency_ms_p50} after={a?.latency_ms_p50} unit="ms" />
            <Delta
              label="DB pool"
              before={b?.pool_utilization_pct}
              after={a?.pool_utilization_pct}
              unit="%"
            />
          </div>
          <p className="mt-2 text-xs text-fg-muted">
            {x.status === "succeeded"
              ? `Recovery verified over ${x.samples.length} measured samples.`
              : `Execution ${x.status}.`}
          </p>
        </>
      )}
      {w.preventive_tasks.length > 0 && (
        <>
          <h3 className="mt-3 text-xs font-semibold tracking-wider text-fg-muted uppercase">
            Preventive tasks
          </h3>
          <ul className="mt-1 flex flex-col gap-1 text-xs text-fg">
            {w.preventive_tasks.map((t) => (
              <li key={t.slug}>• {t.text}</li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
