/**
 * SonarRepoHealthCards.tsx — PH-249 (epic PH-245, child D): a per-repo SonarQube
 * health card grid rendered in BoardDetail's `#quality` tab, ABOVE the existing
 * single-repo `SonarDashboard`. Consumes the PH-246 `BoardResponse.repo_health`
 * breakdown (one entry per linked repository) — the data the single-repo
 * `board.health` strip/dashboard cannot show on a multi-repo board.
 *
 * PURELY PRESENTATIONAL — prop-driven (`{ repoHealth }`). No fetch, no query, no
 * WebSocket: BoardDetail owns the `["board", boardKey]` query + the
 * `sonarqube_synced` invalidation, so a completed scan refreshes `repo_health`
 * → these cards re-render for free (mirrors SonarHealthPanel/SonarDashboard).
 *
 * One `RepoHealthCard` per `repo_health[]` entry:
 *   - header: repo_name (or "Board total" for a null-identity aggregate row) +
 *     a `primary` badge when `repo.is_primary` (PH-251 — sourced from the un-gated
 *     `repo_health[].is_primary`, NOT the member-gated /repositories-derived
 *     primarySlug which 403s for non-member viewers and silently dropped the badge).
 *   - quality-gate pill (shared GATE_MAP tones — DRY via metricMeta, Risk R4).
 *   - 6 metric tiles: bugs / vulnerabilities / code_smells / coverage /
 *     duplications / ncloc.
 *   - per-repo `dashboard_url` external deep link (host-facing — verbatim).
 *   - relative `fetched_at`.
 *
 * Honest states (Risk R3, AC3):
 *   - a never-scanned linked repo (`quality_gate_status == null`) renders the
 *     card frame with an honest "linked — no analysis yet" gate + em-dash metric
 *     tiles. NEVER a fake all-zero card, NEVER a crash.
 *   - `repo_health == []` → render NOTHING (BoardDetail only mounts this when
 *     `repo_health.length > 0`; this guard is belt-and-suspenders). No empty
 *     grid — the existing SonarDashboard covers the board-level surface.
 *
 * Design system: Cyan-on-Black ProjectHub tokens only (`.card`, `.badge`,
 * `.eyebrow`, `.mono`, semantic `text-success/warning/danger`, `bg-*-soft`,
 * `text-text-*`, `bg-raised`, `border-hairline`, `rounded-*`). No baked hexes,
 * no slate/indigo — theme-aware (dark default + `html.light`) for free.
 */
import { ExternalLink, Boxes } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RepoHealth } from "@/types/api";
import {
  formatPercent,
  relativeSynced,
  resolveGate,
} from "./metricMeta";

/** A single per-repo metric tile (label + value, optional danger tint). */
function RepoMetricTile({
  label,
  value,
  tone,
  testid,
}: Readonly<{
  label: string;
  value: string;
  tone?: string;
  testid: string;
}>) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md bg-raised px-2.5 py-2">
      <span className="eyebrow text-text-muted">{label}</span>
      <span
        className={cn("mono text-base font-semibold text-text-primary", tone)}
        data-testid={testid}
      >
        {value}
      </span>
    </div>
  );
}

/** Count value → integer string, null → em-dash (NO DATA, distinct from 0). */
function fmtCount(value: number | null): string {
  return value == null ? "—" : String(value);
}

function RepoHealthCard({
  repo,
  isPrimary,
}: Readonly<{ repo: RepoHealth; isPrimary: boolean }>) {
  // Null identity = the board-level aggregate row (a single-repo board with no
  // per-repo Repository row, e.g. KIM). Never crash on null — title it honestly.
  const title = repo.repo_name ?? "Board total";
  // A linked-but-never-scanned repo has a null gate → honest "no analysis yet".
  const scanned = repo.quality_gate_status != null;
  const gate = resolveGate(repo.quality_gate_status);

  const bugsTone = (repo.bugs ?? 0) > 0 ? "text-danger" : undefined;
  const vulnTone = (repo.vulnerabilities ?? 0) > 0 ? "text-danger" : undefined;

  // A stable test/react key — slug when present, else the project key (aggregate
  // rows have a null slug but always a project_key).
  return (
    <section
      className="card flex flex-col gap-3 p-4"
      data-testid={`repo-health-card-${repo.repo_slug ?? repo.project_key}`}
      aria-label={`SonarQube health: ${title}`}
    >
      {/* Header — repo name + primary badge + gate pill */}
      <div className="flex flex-wrap items-center gap-2">
        <Boxes
          className="h-4 w-4 shrink-0 text-accent-strong"
          aria-hidden="true"
        />
        <h3 className="truncate text-sm font-semibold text-text-primary">
          {title}
        </h3>
        {isPrimary && (
          <span
            className="badge border border-current/30 text-2xs font-medium text-accent bg-accent-soft"
            data-testid="repo-health-primary-badge"
          >
            primary
          </span>
        )}
        <span
          className={cn(
            "badge ml-auto gap-1.5 border border-current/30 text-xs font-medium",
            gate.tone,
          )}
          data-testid="repo-health-gate"
          aria-label={`Quality gate: ${scanned ? gate.label : "no analysis yet"}`}
        >
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
          {scanned ? gate.label : "No analysis yet"}
        </span>
      </div>

      {/* Honest never-scanned copy — NOT a fake all-zero grid (Risk R3 / AC3). */}
      {!scanned && (
        <p
          className="text-xs text-text-muted"
          data-testid="repo-health-no-analysis"
        >
          Linked to{" "}
          <span className="mono text-text-secondary">{repo.project_key}</span> —
          run a scan to populate metrics.
        </p>
      )}

      {/* Metric tiles — em-dash for null (never-scanned) values. */}
      <div className="grid grid-cols-3 gap-2">
        <RepoMetricTile
          label="Bugs"
          value={fmtCount(repo.bugs)}
          tone={bugsTone}
          testid="repo-health-bugs"
        />
        <RepoMetricTile
          label="Vulns"
          value={fmtCount(repo.vulnerabilities)}
          tone={vulnTone}
          testid="repo-health-vulnerabilities"
        />
        <RepoMetricTile
          label="Smells"
          value={fmtCount(repo.code_smells)}
          testid="repo-health-code-smells"
        />
        <RepoMetricTile
          label="Coverage"
          value={formatPercent(repo.coverage)}
          testid="repo-health-coverage"
        />
        <RepoMetricTile
          label="Duplications"
          value={formatPercent(repo.duplicated_lines_density)}
          testid="repo-health-duplications"
        />
        <RepoMetricTile
          label="Lines"
          value={fmtCount(repo.ncloc)}
          testid="repo-health-ncloc"
        />
      </div>

      {/* Footer — relative freshness + per-repo dashboard deep link */}
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1">
        {scanned && repo.fetched_at ? (
          <span
            className="text-2xs text-text-muted"
            data-testid="repo-health-fetched-at"
            title={new Date(repo.fetched_at).toLocaleString()}
          >
            {relativeSynced(repo.fetched_at)}
          </span>
        ) : (
          <span aria-hidden="true" />
        )}
        {repo.dashboard_url && (
          <a
            href={repo.dashboard_url}
            target="_blank"
            rel="noopener noreferrer"
            className="badge gap-1.5 border border-current/30 text-2xs font-medium text-info bg-info-soft hover:opacity-90"
            data-testid="repo-health-dashboard-link"
          >
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
            Open in SonarQube
          </a>
        )}
      </div>
    </section>
  );
}

interface SonarRepoHealthCardsProps {
  /** PH-246 per-repo breakdown (one entry per linked repo, + a possible aggregate). */
  repoHealth: RepoHealth[];
}

export function SonarRepoHealthCards({
  repoHealth,
}: Readonly<SonarRepoHealthCardsProps>) {
  // Belt-and-suspenders: BoardDetail only mounts this when length > 0, but a
  // direct empty render must be a no-op (NO empty grid — Risk R3).
  if (repoHealth.length === 0) return null;

  return (
    <div
      className="space-y-3"
      data-testid="sonar-repo-health-cards"
      aria-label="Per-repository SonarQube health"
    >
      <div className="flex items-center gap-2">
        <span className="eyebrow text-text-muted">Per-repository quality</span>
        <span className="text-2xs text-text-muted">
          {repoHealth.length} {repoHealth.length === 1 ? "repo" : "repos"}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {repoHealth.map((repo) => (
          <RepoHealthCard
            key={repo.repo_slug ?? repo.project_key}
            repo={repo}
            isPrimary={repo.is_primary}
          />
        ))}
      </div>
    </div>
  );
}
