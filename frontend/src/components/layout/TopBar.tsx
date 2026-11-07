import type { BackendHealthState } from "../../hooks/useBackendHealth";
import { StatusBadge, type StatusTone } from "../ui/StatusBadge";

function backendBadge(state: BackendHealthState): { tone: StatusTone; label: string } {
  switch (state.kind) {
    case "loading":
      return { tone: "neutral", label: "API checking…" };
    case "ok":
      return { tone: "success", label: "API ok" };
    case "degraded":
      return { tone: "warning", label: "API degraded" };
    case "unreachable":
      return { tone: "critical", label: "API unreachable" };
  }
}

export function TopBar({ backend }: { backend: BackendHealthState }) {
  const badge = backendBadge(backend);
  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border-subtle bg-panel px-4">
      <div className="flex items-center gap-3">
        <span className="font-mono text-sm font-semibold tracking-[0.3em]">BLACKBOX</span>
        <span className="rounded border border-warning/60 px-1.5 text-[11px] font-medium tracking-wide text-warning">
          SANDBOX
        </span>
      </div>
      <StatusBadge tone={badge.tone} label={badge.label} />
    </header>
  );
}
