export type StatusTone = "success" | "warning" | "critical" | "info" | "neutral";

const TONE_CLASSES: Record<StatusTone, string> = {
  success: "border-success/40 bg-success/12 text-success",
  warning: "border-warning/40 bg-warning/12 text-warning",
  critical: "border-critical/40 bg-critical/12 text-critical",
  info: "border-info/40 bg-info/12 text-info",
  neutral: "border-neutral/40 bg-neutral/12 text-fg-muted",
};

// Status is never conveyed by color alone (docs/06-DESIGN-SYSTEM.md §1.3): each tone has a glyph.
const TONE_GLYPH: Record<StatusTone, string> = {
  success: "●",
  warning: "◆",
  critical: "▲",
  info: "◐",
  neutral: "○",
};

export function StatusBadge({ tone, label }: { tone: StatusTone; label: string }) {
  return (
    <span
      className={`inline-flex h-5 items-center gap-1 rounded border px-1.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      <span aria-hidden="true">{TONE_GLYPH[tone]}</span>
      {label}
    </span>
  );
}
