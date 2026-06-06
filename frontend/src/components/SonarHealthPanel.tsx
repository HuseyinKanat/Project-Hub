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
import { cn } from "@/lib/utils";
import type { BoardHealth } from "@/types/api";

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

/** Integer metric → raw string, null → em-dash. */
function fmtInt(value: number | null): string {
  return value == null ? "—" : String(value);
}

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
}: {
  label: string;
  value: string;
  /** Optional value tint (e.g. text-danger for bugs > 0). Default neutral. */
  tone?: string;
}) {
  return (
    <div className="flex min-w-[64px] flex-col gap-0.5 rounded-md bg-raised px-2.5 py-1.5">
      <span className="eyebrow text-text-muted">{label}</span>
      <span className={cn("mono text-sm font-semibold text-text-primary", tone)}>
        {value}
      </span>
    </div>
  );
}

export function SonarHealthPanel({ health }: { health: BoardHealth | null }) {
  // AC-1: empty state — null health (no scan / no project key). Guard FIRST so
  // we never touch health.* on null (Risk R1).
  if (health == null) {
    return (
      <div
        className="card flex items-center gap-2 border-dashed border-hairline px-4 py-2.5 text-sm text-text-muted"
        data-testid="sonar-health-empty"
        role="status"
        aria-label="SonarQube health: no scan yet"
      >
        <span className="eyebrow text-text-muted">SonarQube</span>
        <span>No SonarQube scan yet</span>
        <span className="text-text-muted/70">
          · Connect a project key to see quality metrics
        </span>
      </div>
    );
  }

  // AC-2: populated state — quality-gate pill + 5 metric tiles + fetched_at.
  const gate =
    health.quality_gate_status != null
      ? (GATE_MAP[health.quality_gate_status] ?? GATE_UNKNOWN)
      : GATE_UNKNOWN;

  const bugsTone = (health.bugs ?? 0) > 0 ? "text-danger" : undefined;
  const vulnTone = (health.vulnerabilities ?? 0) > 0 ? "text-danger" : undefined;

  return (
    <div
      className="card flex flex-wrap items-center gap-3 px-4 py-2.5"
      data-testid="sonar-health-panel"
      role="region"
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

      {/* 5 metric tiles */}
      <div className="flex flex-wrap items-center gap-2">
        <MetricTile label="Bugs" value={fmtInt(health.bugs)} tone={bugsTone} />
        <MetricTile
          label="Vulns"
          value={fmtInt(health.vulnerabilities)}
          tone={vulnTone}
        />
        <MetricTile label="Smells" value={fmtInt(health.code_smells)} />
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
    </div>
  );
}
