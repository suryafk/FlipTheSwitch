/** Time helpers for API timestamps (UTC, sometimes without a zone suffix). */

/** The API returns UTC timestamps, sometimes without a zone ("2026-11-07 11:40:31"). */
export function parseUtc(value: string | null | undefined): Date | null {
  if (!value) return null;
  const v = /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function clock(value: string | null | undefined) {
  const d = parseUtc(value);
  return d ? d.toLocaleTimeString([], { hour12: false }) : "—";
}

export function duration(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}
