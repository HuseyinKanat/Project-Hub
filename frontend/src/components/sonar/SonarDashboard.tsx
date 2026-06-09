/**
 * SonarDashboard.tsx — PH-241 (epic PH-238, child C): the detailed in-app
 * SonarQube quality dashboard rendered in BoardDetail's new "Quality" tab
 * (`#quality`). A native, branded surface in the ProjectHub cyan-on-black design
 * system that resembles SonarQube's own dashboard.
 *
 * PURELY PRESENTATIONAL — prop-driven (`{ health, boardKey, projectKey }`),
 * mirroring SonarHealthPanel. BoardDetail owns the `["board", boardKey]` query +
 * the `sonarqube_synced` WS invalidation; this component reads the SAME cache
 * (board.health + the sonar-issues live-count family), so a scan completing
 * refreshes the grid + counts with NO new wiring (live update for free).
 *
 * Layout:
 *   - Header: title + a host-facing "Open in SonarQube" external link.
 *   - Quality-gate hero: Passed / Failed / Warning / Unknown (GATE_MAP tones),
 *     with its description; on ERROR it prompts opening the SonarQube dashboard
 *     for per-condition detail (BoardHealth carries only the aggregate gate).
 *   - 6-metric responsive card grid (bugs/vulns/smells clickable, the rest
 *     informational), each with value + canonical description.
 *   - Lifted SonarIssueDrawer drill-down (openType + triggerRefs + focus-restore
 *     handleClose — same a11y contract as SonarHealthPanel).
 *
 * Honest states (Risk R3): null health degrades to the PH-235 copy — "linked,
 * no analysis yet · run a scan" when a project key is set, else "connect a
 * project key" — NEVER a blank screen or a fake all-zero grid. This is KIM's
 * current state (no analysis), the most likely live condition.
 *
 * Design system: Cyan-on-Black ProjectHub tokens only — no baked hexes, no
 * slate/indigo; theme-aware (dark + `html.light`) for free.
 */
import { useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSonarLiveCounts } from "@/hooks/useSonarLiveCounts";
import { useSonarIssues } from "@/hooks/useSonarIssues";
import type { BoardHealth, SonarIssueType } from "@/types/api";
import { SonarIssueDrawer } from "@/components/SonarIssueDrawer";
import { MetricCard } from "./MetricCard";
import {
  GATE_META,
  METRIC_META,
  resolveGate,
  type MetricKey,
} from "./metricMeta";

interface SonarDashboardProps {
  health: BoardHealth | null;
  /** Needed by the issue drawer + live-count queries. */
  boardKey: string;
  /** Resolved SonarQube project key (or null) — drives honest empty-state copy. */
  projectKey: string | null;
}

export function SonarDashboard({
  health,
  boardKey,
  projectKey,
}: Readonly<SonarDashboardProps>) {
  // Live issue counts from the SAME source the drawer reads — reconciled
  // `live ?? health.*` so the card counts can never contradict the drill-down
  // (Risk R2). Called unconditionally (Rules of Hooks) before any early return.
  const liveCounts = useSonarLiveCounts(boardKey);

  // dashboard_url is host-facing — read it from the BUG issues query (the same
  // source SonarIssueDrawer uses for its "View in SonarQube" link). Lazy/cached;
  // null until resolved or when no project key. No extra endpoint (Risk R6).
  const bugIssues = useSonarIssues(boardKey, health != null ? "BUG" : null);
  const dashboardUrl = bugIssues.data?.dashboard_url ?? null;

  // Lifted drawer state — which issue type is open (null = none). The drawer
  // (and its lazy query) mounts only on a card click.
  const [openType, setOpenType] = useState<SonarIssueType | null>(null);
  const triggerRefs = useRef<
    Partial<Record<SonarIssueType, HTMLButtonElement | null>>
  >({});

  const handleClose = () => {
    const last = openType;
    setOpenType(null);
    // Restore focus to the triggering card (a11y parity with the strip, R5).
    if (last) triggerRefs.current[last]?.focus();
  };

  // ── Honest empty state (Risk R3): null health → no analysis yet. ──────────
  if (health == null) {
    const linked = Boolean(projectKey);
    return (
      <output
        className="card flex flex-col items-center gap-2 border-dashed border-hairline px-6 py-12 text-center"
        data-testid="sonar-dashboard-empty"
        aria-label={
          linked
            ? "SonarQube dashboard: linked, no analysis yet"
            : "SonarQube dashboard: no scan yet"
        }
      >
        <span className="eyebrow text-text-muted">SonarQube Quality</span>
        {linked ? (
          <>
            <p className="text-sm text-text-secondary" data-testid="sonar-dashboard-no-analysis">
              Linked to <span className="mono text-text-primary">{projectKey}</span> — no
              analysis yet
            </p>
            <p className="text-xs text-text-muted">
              Run a scan from Settings → SonarQube to populate quality metrics.
            </p>
          </>
        ) : (
          <>
            <p className="text-sm text-text-secondary">No SonarQube scan yet</p>
            <p className="text-xs text-text-muted">
              Connect a project key in Settings → SonarQube to see quality
              metrics.
            </p>
          </>
        )}
      </output>
    );
  }

  // ── Populated state ───────────────────────────────────────────────────────
  const gateMeta = GATE_META;
  const gateStatus = health.quality_gate_status;
  const gate = resolveGate(gateStatus);
  const gateFailed = gateStatus === "ERROR";

  // Reconciled issue counts (live ?? cached) for the three issue-backed cards.
  const liveByType: Record<SonarIssueType, number | null> = {
    BUG: liveCounts.counts.BUG ?? health.bugs,
    VULNERABILITY: liveCounts.counts.VULNERABILITY ?? health.vulnerabilities,
    CODE_SMELL: liveCounts.counts.CODE_SMELL ?? health.code_smells,
  };

  /** Resolve a metric card's value: issue-backed → reconciled count, else raw. */
  const valueFor = (key: MetricKey, issueType: SonarIssueType | null): number | null => {
    if (issueType != null) return liveByType[issueType];
    const raw = health[key];
    return typeof raw === "number" ? raw : null;
  };

  // The 6 grid metrics (everything after the quality-gate hero).
  const gridMeta = METRIC_META.slice(1);

  return (
    <div
      className="space-y-4"
      data-testid="sonar-dashboard"
      aria-label="SonarQube quality dashboard"
    >
      {/* Header — title + open-in-SonarQube external link */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="eyebrow text-text-muted">SonarQube</span>
          <h2 className="text-lg font-semibold text-text-primary">
            Quality Dashboard
          </h2>
        </div>
        {dashboardUrl && (
          <a
            href={dashboardUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="badge gap-1.5 border border-current/30 text-xs font-medium text-info bg-info-soft hover:opacity-90"
            data-testid="sonar-dashboard-open-link"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            Open in SonarQube
          </a>
        )}
      </div>

      {/* Quality-gate hero */}
      <section
        className="card flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between"
        data-testid="sonar-dashboard-gate-hero"
        aria-label={`Quality gate: ${gate.label}`}
      >
        <div className="flex items-center gap-4">
          <gateMeta.icon
            className="h-8 w-8 shrink-0 text-accent-strong"
            aria-hidden="true"
          />
          <div className="space-y-1">
            <span className="eyebrow text-text-muted">{gateMeta.label}</span>
            <p className="max-w-xl text-xs leading-snug text-text-secondary">
              {gateMeta.description}
            </p>
          </div>
        </div>
        <div className="flex flex-col items-start gap-1.5 sm:items-end">
          <span
            className={cn(
              "badge gap-1.5 border border-current/30 text-sm font-semibold",
              gate.tone,
            )}
            data-testid="sonar-dashboard-gate-status"
          >
            <span
              aria-hidden="true"
              className="h-2 w-2 rounded-full bg-current"
            />
            {gate.label}
          </span>
          {gateFailed && dashboardUrl && (
            <a
              href={dashboardUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-2xs text-info hover:opacity-90"
              data-testid="sonar-dashboard-gate-detail-link"
            >
              See failing conditions in SonarQube →
            </a>
          )}
        </div>
      </section>

      {/* Metric grid — reflows on narrow viewports */}
      <div
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
        data-testid="sonar-dashboard-grid"
      >
        {gridMeta.map((meta) => (
          <MetricCard
            key={meta.key}
            meta={meta}
            value={valueFor(meta.key, meta.issueType)}
            onOpen={meta.issueType != null ? setOpenType : undefined}
            triggerRef={
              meta.issueType != null
                ? (el) => {
                    if (meta.issueType) triggerRefs.current[meta.issueType] = el;
                  }
                : undefined
            }
          />
        ))}
      </div>

      {/* Issue drill-down drawer — mounted only when a card is open (lazy). */}
      {openType && (
        <SonarIssueDrawer
          boardKey={boardKey}
          type={openType}
          onClose={handleClose}
        />
      )}
    </div>
  );
}
