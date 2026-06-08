/**
 * SonarHealthPanel.tsx — PH-196 (epic PH-191, CHILD 4): presentational
 * SonarQube board-health strip rendered above the BoardDetail tab strip.
 *
 * PURELY PRESENTATIONAL — prop-driven (`{ health: BoardHealth | null }`).
 * No data fetching, no WebSocket inside it: BoardDetail owns the
 * `["board", boardKey]` query + the `sonarqube_synced` invalidation, and
 * passes `boardQuery.data?.health ?? null` down (mirrors the LiveStatus
 * prop-driven pattern, components/LiveStatus.tsx).
 *
 * Design system: Cyan-on-Black ProjectHub tokens only (`.card`, `.badge`,
 * `.eyebrow`, `.mono`, semantic `text-success/warning/danger`, `bg-*-soft`,
 * `text-text-*`, `bg-surface/raised/inset`, `border-hairline`,
 * `rounded-pill/md/lg`). No slate/indigo utilities, no baked hexes — theme
 * aware (dark default + `html.light`) for free.
 */
import { useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useSonarLiveCounts } from "@/hooks/useSonarLiveCounts";
import type { BoardHealth, SonarIssueType } from "@/types/api";
import { SonarIssueDrawer } from "./SonarIssueDrawer";

/**
 * Quality-gate pill descriptor keyed by the raw `quality_gate_status` value.
 * `null` (health row exists but gate unknown) falls back to the muted "Unknown"
 * tone. Mirrors the LiveStatus soft-tone badge treatment.
 */
const GATE_MAP: Record<string, { label: string; tone: string }> = {
  OK: { label: "Passed", tone: "text-success bg-success-soft" },
  ERROR: { label: "Failed", tone: "text-danger bg-danger-soft" },
  WARN: { label: "Warning", tone: "text-warning bg-warning-soft" },
};

const GATE_UNKNOWN = { label: "Unknown", tone: "text-text-muted bg-raised" };

/** Percent float 0..100 → `NN.N%`, null → em-dash. (Already a percent — do NOT ×100.) */
function fmtPct(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}

/**
 * Relative "Synced 3m ago" timestamp from an ISO string. Tiny inline formatter
 * (no new dep) using Intl.RelativeTimeFormat; falls back to a locale string for
 * unparseable input.
 */
function relativeSynced(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffMs = Date.now() - then;
  const sec = Math.round(diffMs / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let rel: string;
  if (sec < 60) rel = rtf.format(-sec, "second");
  else if (sec < 3600) rel = rtf.format(-Math.round(sec / 60), "minute");
  else if (sec < 86400) rel = rtf.format(-Math.round(sec / 3600), "hour");
  else rel = rtf.format(-Math.round(sec / 86400), "day");
  return `Synced ${rel}`;
}

function MetricTile({
  label,
  value,
  tone,
}: Readonly<{
  label: string;
  value: string;
  /** Optional value tint (e.g. text-danger for bugs > 0). Default neutral. */
  tone?: string;
}>) {
  return (
    <div className="flex min-w-[64px] flex-col gap-0.5 rounded-md bg-raised px-2.5 py-1.5">
      <span className="eyebrow text-text-muted">{label}</span>
      <span className={cn("mono text-sm font-semibold text-text-primary", tone)}>
        {value}
      </span>
    </div>
  );
}

/**
 * Issue-backed metric tile (PH-204): a real focusable `<button>` that opens the
 * SonarIssueDrawer for its issue `type`. Only Bugs / Vulns / Smells use this —
 * Coverage / Duplications stay plain `MetricTile`s (no issue list backs them,
 * Risk R2). Disabled when the count is 0 or null so we never open an empty
 * drawer (Risk R1). Keyboard-native (Enter/Space) with the global focus ring.
 */
function ClickableMetricTile({
  label,
  count,
  type,
  tone,
  onOpen,
  triggerRef,
}: Readonly<{
  label: string;
  /** Raw issue count (null when SonarQube has no data for this metric). */
  count: number | null;
  type: SonarIssueType;
  tone?: string;
  onOpen: (type: SonarIssueType) => void;
  triggerRef: (el: HTMLButtonElement | null) => void;
}>) {
  const disabled = count == null || count === 0;
  const disabledValue = count == null ? "no data" : "0";
  const ariaLabel = disabled
    ? `${label}: ${disabledValue}`
    : `View ${count} ${label.toLowerCase()}`;
  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={() => onOpen(type)}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-label={ariaLabel}
      data-testid={`sonar-tile-${type}`}
      className={cn(
        "flex min-w-[64px] flex-col gap-0.5 rounded-md bg-raised px-2.5 py-1.5 text-left transition-colors",
        "enabled:cursor-pointer enabled:hover:bg-surface",
        "disabled:cursor-default disabled:opacity-70",
      )}
    >
      <span className="eyebrow text-text-muted">{label}</span>
      <span className={cn("mono text-sm font-semibold text-text-primary", tone)}>
        {count == null ? "—" : String(count)}
      </span>
    </button>
  );
}

export function SonarHealthPanel({
  health,
  boardKey,
  projectKey = null,
}: Readonly<{
  health: BoardHealth | null;
  /** PH-204: needed by the issue drawer's lazy query. */
  boardKey: string;
  /**
   * PH-235: the board's resolved SonarQube project key (or null). Splits the
   * `health == null` empty state into honest copy: a SET key means "linked —
   * no analysis yet" (NOT "connect a project key"); null keeps the original
   * unconfigured copy. Defaults to null so existing callers stay backward-safe.
   */
  projectKey?: string | null;
}>) {
  // PH-218: live per-type issue `total` (BUG/VULNERABILITY/CODE_SMELL) from the
  // same endpoint the drawer reads, so the tile counts can never contradict the
  // drill-down list. Called unconditionally (Rules of Hooks) BEFORE the
  // health == null early return; reconciled per-tile as `live ?? health.*` so a
  // loading/errored live fetch falls back to the cached count (never blank).
  const liveCounts = useSonarLiveCounts(boardKey);

  // PH-204: which issue drawer is open (null = none). Lazy — the drawer (and
  // therefore the issues query) is mounted only when a tile is clicked.
  const [openType, setOpenType] = useState<SonarIssueType | null>(null);
  // Trigger refs per type so we can restore focus to the tile on drawer close.
  const triggerRefs = useRef<Partial<Record<SonarIssueType, HTMLButtonElement | null>>>(
    {},
  );

  const handleClose = () => {
    const last = openType;
    setOpenType(null);
    // Restore focus to the triggering tile (a11y, Risk R6).
    if (last) triggerRefs.current[last]?.focus();
  };

  // AC-1: empty state — null health (no scan / no project key). Guard FIRST so
  // we never touch health.* on null (Risk R1). PH-235: split the copy on whether
  // a project key IS configured — a SET key is honestly "linked — no analysis
  // yet" (the server is up, the board just wasn't scanned), NOT "connect a key".
  if (health == null) {
    const linked = Boolean(projectKey);
    return (
      <output
        className="card flex items-center gap-2 border-dashed border-hairline px-4 py-2.5 text-sm text-text-muted"
        data-testid="sonar-health-empty"
        aria-label={
          linked
            ? "SonarQube health: linked, no analysis yet"
            : "SonarQube health: no scan yet"
        }
      >
        <span className="eyebrow text-text-muted">SonarQube</span>
        {linked ? (
          <>
            <span data-testid="sonar-health-no-analysis">
              Linked to <span className="mono">{projectKey}</span> — no analysis yet
            </span>
            <span className="text-text-muted/70">· run a scan to see metrics</span>
          </>
        ) : (
          <>
            <span>No SonarQube scan yet</span>
            <span className="text-text-muted/70">
              · Connect a project key to see quality metrics
            </span>
          </>
        )}
      </output>
    );
  }

  // AC-2: populated state — quality-gate pill + 5 metric tiles + fetched_at.
  const gate =
    health.quality_gate_status != null
      ? (GATE_MAP[health.quality_gate_status] ?? GATE_UNKNOWN)
      : GATE_UNKNOWN;

  // PH-218: reconcile the three issue counts — prefer the live total, fall back
  // to the poller-cached board.health count while the live fetch is
  // loading/errored (SonarQube down → still render cached numbers, never blank).
  // Result stays `number | null`, exactly what ClickableMetricTile expects.
  const bugsCount = liveCounts.counts.BUG ?? health.bugs;
  const vulnCount = liveCounts.counts.VULNERABILITY ?? health.vulnerabilities;
  const smellsCount = liveCounts.counts.CODE_SMELL ?? health.code_smells;

  const bugsTone = (bugsCount ?? 0) > 0 ? "text-danger" : undefined;
  const vulnTone = (vulnCount ?? 0) > 0 ? "text-danger" : undefined;

  return (
    <section
      className="card flex flex-wrap items-center gap-3 px-4 py-2.5"
      data-testid="sonar-health-panel"
      aria-label="SonarQube board health"
    >
      <span className="eyebrow text-text-muted">SonarQube</span>

      {/* Quality-gate pill — soft-tone badge, mirrors LiveStatus */}
      <span
        className={cn(
          "badge gap-1.5 border border-current/30 text-xs font-medium",
          gate.tone,
        )}
        data-testid="sonar-quality-gate"
        aria-label={`Quality gate: ${gate.label}`}
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
        {gate.label}
      </span>

      {/* 5 metric tiles — Bugs/Vulns/Smells are clickable (PH-204, open a drawer);
          Coverage/Duplications stay plain (aggregate %, no issue list — Risk R2). */}
      <div className="flex flex-wrap items-center gap-2">
        <ClickableMetricTile
          label="Bugs"
          count={bugsCount}
          type="BUG"
          tone={bugsTone}
          onOpen={setOpenType}
          triggerRef={(el) => (triggerRefs.current.BUG = el)}
        />
        <ClickableMetricTile
          label="Vulns"
          count={vulnCount}
          type="VULNERABILITY"
          tone={vulnTone}
          onOpen={setOpenType}
          triggerRef={(el) => (triggerRefs.current.VULNERABILITY = el)}
        />
        <ClickableMetricTile
          label="Smells"
          count={smellsCount}
          type="CODE_SMELL"
          onOpen={setOpenType}
          triggerRef={(el) => (triggerRefs.current.CODE_SMELL = el)}
        />
        <MetricTile label="Coverage" value={fmtPct(health.coverage)} />
        <MetricTile
          label="Duplications"
          value={fmtPct(health.duplicated_lines_density)}
        />
      </div>

      {/* fetched_at relative timestamp — right-aligned */}
      <span
        className="ml-auto text-2xs text-text-muted"
        data-testid="sonar-fetched-at"
        title={new Date(health.fetched_at).toLocaleString()}
      >
        {relativeSynced(health.fetched_at)}
      </span>

      {/* PH-204: issue drill-down drawer — mounted only when a tile is open,
          so the lazy issues query never fires until first click. */}
      {openType && (
        <SonarIssueDrawer
          boardKey={boardKey}
          type={openType}
          onClose={handleClose}
        />
      )}
    </section>
  );
}
