# 06 — BLACKBOX Design System

A dark-first visual language for an enterprise SRE command center. It is dense, calm and legible under stress.

Companions: [`06-UX-SPECIFICATION.md`](06-UX-SPECIFICATION.md) · [`06-SCREEN-FLOWS.md`](06-SCREEN-FLOWS.md).

## 0. Principles

1. **Evidence over decoration.** Every pixel shows state, provenance or an action. No gradients, glows, illustrations, or "AI sparkle" imagery.
2. **Provenance is visible.** Anything that came from Switch carries a Switch provenance mark, such as `SW · seq 4813`. Anything BLACKBOX derived is marked as such. Simulated data is badged.
3. **Color is never the only signal.** Every status pairs a color with an icon or shape and a text label. Every agent has a color, a monogram, a glyph and a name.
4. **Calm by default, loud only for decisions.** Critical red and approval amber are reserved. If everything is highlighted, nothing is.
5. **Motion only means "live".** The only recurring animation is the live-stream pulse dot. Everything else is an instant state change. `prefers-reduced-motion` disables the pulse as well.

---

## 1. Color

### 1.1 Neutral surfaces (dark theme, the default)

| Token | Hex | Use |
|---|---|---|
| `bg.canvas` | `#0B0E13` | App background |
| `bg.panel` | `#11161D` | Panels and cards |
| `bg.raised` | `#161C25` | Hover, popovers, table header |
| `bg.inset` | `#0E1218` | Code blocks, inputs, feed background |
| `border.subtle` | `#1F2733` | Panel borders, dividers |
| `border.strong` | `#2C3644` | Focused containers, table grid |
| `text.primary` | `#E6EAF0` | Primary text |
| `text.secondary` | `#A3ADBB` | Labels, metadata |
| `text.tertiary` | `#6B7685` | Timestamps, hints, disabled |
| `text.inverse` | `#0B0E13` | Text on solid status fills |

### 1.2 Brand and interactive

| Token | Hex | Use |
|---|---|---|
| `accent.primary` | `#3B82F6` (electric blue) | Primary buttons, links, focus ring, selected nav |
| `accent.primary.hover` | `#2563EB` | |
| `focus.ring` | `#60A5FA` at 2 px | Keyboard focus, always visible |

### 1.3 Semantic status

These are reserved. Do not use them decoratively.

| Token | Hex | Meaning | Paired icon |
|---|---|---|---|
| `status.critical` | `#EF4444` | SEV-1, failure, rejected, refused, 5xx breach | ▲ filled triangle |
| `status.warning` | `#F59E0B` | **Pending human decision**, SEV-2, degraded, expiring | ◆ diamond |
| `status.success` | `#22C55E` | Healthy, approved, verified, passed | ● filled circle + ✓ |
| `status.info` | `#3B82F6` | In progress, informational | ◐ half circle |
| `status.neutral` | `#6B7685` | Closed, expired, archived, unknown | ○ hollow circle |
| `status.simulated` | `#94A3B8` on a dashed border | `[SIMULATED]` badge | ⧉ |

Tints for backgrounds are 12% alpha (for example `status.critical/12` = `rgba(239,68,68,0.12)`). Text sits on tints only in `text.primary`.

### 1.4 Agent identity colors

These are chosen so they do not collide with the status colors.

| Agent | Color | Hex | Monogram | Glyph | Name label |
|---|---|---|---|---|---|
| Sentinel | Teal | `#2DD4BF` | `SN` | radar (concentric arcs) | BLACKBOX Sentinel |
| RootCause | Violet | `#A78BFA` | `RC` | magnifier | BLACKBOX RootCause |
| Recovery | Magenta | `#F472B6` | `RV` | shield | BLACKBOX Recovery |
| Control (system) | Slate | `#94A3B8` | `CT` | gear | BLACKBOX Control · system |
| Human | White outline | `#E6EAF0` | Initials | person | Display name + `HUMAN` tag |
| Executor (not a Switch participant) | Steel | `#64748B` | `EX` | lock | Executor · BLACKBOX |

**Rule:** agent colors appear only in a **3 px left rail**, the **monogram avatar**, and **from/to chips**. They never fill large areas.

### 1.5 Contrast

All text tokens on `bg.panel` meet WCAG AA (4.5:1 for body, 3:1 for ≥ 18 px). Agent colors on `bg.panel` are ≥ 3:1, used for non-text marks and for large monograms.

### 1.6 Light theme (secondary; for the printed postmortem)

The light theme maps the same tokens. The postmortem print stylesheet uses the light theme automatically.

---

## 2. Typography

| Role | Font | Size / line-height | Weight | Use |
|---|---|---|---|---|
| Display | Inter | 24 / 32 | 600 | Screen titles (`INC-1042 · payment-service`) |
| Heading | Inter | 18 / 26 | 600 | Panel titles |
| Subheading | Inter | 14 / 20 | 600, caps tracking +0.04em | Section labels (`EVIDENCE`, `HANDOFF`) |
| Body | Inter | 14 / 20 | 400 | Feed messages, descriptions |
| Small | Inter | 12 / 16 | 400–500 | Metadata, timestamps, badges |
| Mono | JetBrains Mono | 13 / 20 | 400 | Code, diffs, ids, hashes, log lines |
| Metric | JetBrains Mono, tabular numerals | 28 / 32 (KPI), 20 / 24 (tile) | 600 | Numbers that change live, without layout shift |

**Rules:**

- IDs, hashes, SHAs, `request_id`s and timestamps always use mono.
- Hashes are shown shortened, as the first 12 characters followed by `…` (`sha256:3f9a0c1d2e3b…`), with copy-full on click.
- Timestamps are absolute UTC (`10:41:12Z`), with relative time in a tooltip.
- **Presenter mode** (for recording) scales everything by 1.15 and hides secondary metadata.

---

## 3. Spacing, grid, shape

| Token | Value |
|---|---|
| Base unit | 4 px |
| Spacing scale | 4, 8, 12, 16, 20, 24, 32, 40, 48 |
| Panel padding | 16 (dense), 20 (default) |
| Gutter between panels | 12 |
| Radius | 6 (panels, cards, inputs), 4 (badges, chips), 999 (avatars and status dots only) |
| Border | 1 px `border.subtle`. Focused or selected: 1 px `border.strong` plus a 2 px accent rail. |
| Elevation | None (flat). Popovers use `bg.raised` plus a 1 px border and a single 0 4 16 rgba(0,0,0,.4) shadow. |
| Layout grid | 12 columns, max content width 1920, min 360 |
| Breakpoints | `sm` 640 · `md` 1024 · `lg` 1440 · `xl` 1920 |
| Density | Table rows 36 px (default) / 32 px (compact); feed row minimum 44 px |

---

## 4. Component styling

| Component | Spec |
|---|---|
| **Top bar** (48 px) | Product mark `BLACKBOX` (mono, letter-spaced); environment pill (`SANDBOX · staging-sandbox`, amber outline); Switch connection status; Mattermost bridge status; clock (UTC); user and role |
| **Left nav** (56 px collapsed / 220 px expanded) | Icons with labels: Dashboard, Incidents, Agents, History. Active item: accent rail plus `bg.raised`. |
| **Panel** | `bg.panel`, 1 px border, 6 radius. Header 40 px: title on the left, actions on the right. Can collapse. |
| **KPI tile** | Label (small caps), metric value (Metric 28), delta line (small, colored by semantics), optional 60-point sparkline |
| **Badge** | Height 20, padding 0 6, radius 4, 12 px medium; outline variant for provenance (`SW · seq 4813`), solid tint variant for status |
| **Chip** (agent/from-to) | Monogram dot plus name; agent-colored 1 px border |
| **Button** | Primary: accent fill, inverse text. Secondary: outline `border.strong`. Destructive (Revoke, Reset): outline `status.critical`, needs confirmation. Height 32 (default) / 28 (compact). |
| **Table** | Sticky header in `bg.raised`; zebra off; hover in `bg.raised`; numeric columns right-aligned in mono; row click opens a drawer |
| **Drawer** | Right side, 480–640 px, for detail without losing context |
| **Toast** | Bottom right; only for user-initiated results (copied, revoke submitted). System events go to the feed, never to toasts. |
| **Banner** | Full width under the top bar for degraded dependencies (Switch, bridge, stream). Amber or red. Not dismissible while active. |
| **Skeleton** | `bg.raised` blocks at the final layout size; no shimmer animation (static at 60% opacity) |
| **Stepper** | Vertical. Each step shows an icon state (pending ○, active ◐, done ✓, failed ▲, skipped –), a label, a timestamp and a detail line. |
| **Countdown** | Mono `mm:ss`. Turns amber under 5 min and red under 1 min, with the text "expires 10:50:00Z". |
| **Code block** | `bg.inset`, mono 13, line numbers in `text.tertiary`, copy button on hover |

---

## 5. Status indicators

### 5.1 Incident state → visual

| State(s) | Chip color | Icon | Label |
|---|---|---|---|
| DETECTED, INVESTIGATING | `info` | ◐ | Investigating |
| ROOT_CAUSE_IDENTIFIED, REMEDIATION_STAGED, VALIDATING | `info` | ◐ | Remediating |
| AWAITING_APPROVAL, APPROVAL_EXPIRED (re-request) | `warning` | ◆ | Awaiting approval |
| APPROVED, EXECUTING, VERIFYING | `info` | ◐ | Executing |
| RESOLVED, POSTMORTEM_GENERATED, PREVENTIVE_TASKS_CREATED | `success` | ✓ | Resolved |
| REJECTED, NEEDS_REVISION, REVOKED | `warning` | ↺ | Revising |
| NEEDS_HUMAN | `warning` (solid) | ✋ | Needs human |
| ROLLING_BACK, ROLLED_BACK | `critical` outline | ↶ | Rolled back |
| FAILED | `critical` solid | ▲ | Failed |
| REOPENED | `info` | ⟲ | Reopened |

The raw state name is always available in a tooltip and in the timeline.

### 5.2 Severity

| Severity | Style |
|---|---|
| SEV-1 | Solid `critical`, text `SEV-1` |
| SEV-2 | `warning` solid |
| SEV-3 | `info` outline |
| SEV-4 | `neutral` outline |

### 5.3 Liveness

| Situation | Indicator |
|---|---|
| Live stream connected | Green dot with a 2 s pulse (disabled under reduced motion) and "LIVE · seq 4813" |
| Reconnecting | Amber hollow dot, "Reconnecting… last seq 4813" |
| Disconnected | Red, "Stream offline", plus a banner |
| Agent connected (WebSocket to Switch) | Solid dot in the agent's color |
| Agent stale (no heartbeat > 10 s) | Hollow dot plus an amber "stale" badge |

---

## 6. Agent identity system

```
┌──────────────────────────────────────────────────────────────┐
│▌ (SN)  BLACKBOX Sentinel   · investigation   SW·seq 4821  10:31:05Z │
│▌        INC-1042 SEV-1: 5xx 17.4%, p50 3.9 s, pool 10/10 …       │
└──────────────────────────────────────────────────────────────┘
 ▲ 3px rail in agent color   (SN) = 24px monogram avatar on agent-color/16 tint
```

- **Avatar:** a 24 px circle. Its fill is the agent color at 16% with a 1 px border in the agent color. The monogram is in the agent color. At 32 px or larger it shows the glyph instead of the monogram.
- **Name:** always shown in full on the first row of a group. Later rows from the same actor within 2 minutes are grouped and show only the rail.
- **Humans:** a neutral avatar with initials, the display name, and a `HUMAN` outline tag. If the human is the incident commander, an `IC` tag is added. A human not on the approver allowlist gets no `IC` tag.
- **System (Control):** the slate rail and the `SYSTEM` tag. Its rows use a smaller 12 px body, so they recede visually.
- **Executor:** not a Switch participant, so it never appears as a chat author. It appears only in execution rows with the `BLACKBOX` provenance badge.

---

## 7. Data visualization

| Rule | Detail |
|---|---|
| Library | Recharts, in the React stack |
| Series colors | Metrics use neutral-blue `#60A5FA` (5xx %), `#C4B5FD` (p50 ms), `#5EEAD4` (pool %). These are *metric* hues, softer than the agent colors. One axis per chart; no dual axes. |
| Thresholds | Dashed horizontal lines: alert thresholds in `warning` (5xx 5%, p50 1000 ms, pool 90%); the SLO target in `success` at 50% opacity. Each is labelled at the right edge. |
| Event markers | Vertical lines with a top flag: `deploy #842` (critical), `alert` (warning), `approval` (warning to success), `deploy #843` (info), `verified` (success). Clicking a flag jumps to the feed row. |
| Fills | None. Lines are 1.5 px. No area gradients. |
| Time axis | UTC `HH:mm:ss`; window selector 5m / 15m / 1h. Live charts scroll; a paused state appears on hover. |
| Before/after tiles | Two mono numbers with an arrow and the delta (`17.4% → 0.2%`). Each number links to its `sampled_at` sample. Labelled "measured". |
| Sparklines | 60 points, 1 px, no axes, current value dot |
| Tables over charts | Every chart has a "view data" toggle that shows the raw samples (accessibility and auditability) |
| Simulated data | Seeded historical incidents are rendered with dashed lines and a `SAMPLE DATA` badge |

---

## 8. Code diff styling

| Element | Style |
|---|---|
| Container | `bg.inset`, 1 px border, mono 13 / 20 |
| File header | Path (mono), change counts `+3 −1` (success / critical text), file status badge (M/A/D), "allowlisted ✓" badge |
| Hunk header | `@@ -3,7 +3,7 @@` in `text.tertiary` on `bg.raised` |
| Added line | Background `rgba(34,197,94,0.12)`, gutter `+` in `#22C55E`, text `text.primary` |
| Removed line | Background `rgba(239,68,68,0.12)`, gutter `−` in `#EF4444` |
| Intra-line change | A stronger tint at 0.28 alpha over the changed tokens |
| Line numbers | Two columns (old/new), `text.tertiary`, unselectable |
| Syntax highlighting | A muted palette: keywords `#C4B5FD`, strings `#86EFAC`, numbers `#FBBF24`, comments `#6B7685`, YAML keys `#93C5FD`. Lower saturation than the status colors. |
| Modes | Unified (default), split at ≥ 1440 px, whole-file view |
| Long diffs | Collapse unchanged context beyond 3 lines, with "expand ↕" controls |
| Integrity strip | Above the diff: `base 4be2…` → `commit 9c1e…` · `patch sha256:3f9a0c1d2e3b…` · `computed by BLACKBOX` |

---

## 9. Iconography and imagery

- One line-icon set (Lucide), 16 / 20 px, 1.5 px stroke.
- No illustrations, mascots or robot imagery.
- Empty states are text plus one icon.

## 10. Accessibility

- AA contrast throughout.
- Full keyboard navigation, with a visible focus ring.
- The feed is an ARIA live region, `polite`. Decisions are announced `assertive`.
- Every chart has a data-table equivalent.
- Hit targets are at least 32 px (28 px in compact tables, with the row as the target).
- Status is never conveyed by color alone (§1.3 icons, §5 labels).
