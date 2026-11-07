import { Panel } from "./ui/Panel";
import { StatusBadge } from "./ui/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { getPreflight, type PreflightCheck } from "../lib/api/preflight";

function mark(c: PreflightCheck) {
  if (c.ok) return { glyph: "✓", cls: "text-success" };
  if (c.ok === false && c.critical) return { glyph: "✗", cls: "text-critical" };
  return { glyph: "!", cls: "text-warning" };
}

/** Demo readiness, from real checks of Switch, Slack, the service, the stack and the store. */
export function PreflightPanel() {
  const state = usePolling(getPreflight, 15_000, "preflight");
  const badge =
    state.kind === "ok" ? (
      <StatusBadge
        tone={state.data.ready ? "success" : "critical"}
        label={state.data.ready ? "Ready for the demo" : "Not ready"}
      />
    ) : null;
  return (
    <Panel title="Demo pre-flight" actions={badge}>
      {state.kind === "loading" && <p className="text-sm text-fg-subtle">Running checks…</p>}
      {state.kind === "error" && (
        <p className="text-sm text-critical">Pre-flight unavailable: {state.error.message}</p>
      )}
      {state.kind === "ok" && (
        <>
          <ul className="flex flex-col gap-1 text-sm">
            {state.data.checks.map((c) => {
              const m = mark(c);
              return (
                <li key={c.name} className="flex gap-2">
                  <span aria-hidden="true" className={`w-4 shrink-0 font-mono ${m.cls}`}>
                    {m.glyph}
                  </span>
                  <span className="w-60 shrink-0 text-fg">{c.name}</span>
                  <span className="min-w-0 flex-1 break-words text-fg-muted">
                    {c.detail}
                    {!c.ok && c.fix && (
                      <span className="block text-xs text-fg-subtle">→ {c.fix}</span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
          {!state.data.ready && (
            <p className="mt-3 text-xs text-fg-subtle">
              Fix everything at once with <span className="font-mono">make demo-reset</span>.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}
