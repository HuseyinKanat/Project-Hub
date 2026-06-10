/**
 * SonarRepoHealthCards.tsx — PH-249 (epic PH-245, child D): a per-repo SonarQube
 * health card grid rendered in BoardDetail's `#quality` tab, ABOVE the existing
 * single-repo `SonarDashboard`. Consumes the PH-246 `BoardResponse.repo_health`
 * breakdown (one entry per linked repository) — the data the single-repo
 * `board.health` strip/dashboard cannot show on a multi-repo board.
 *
 * PH-256 (epic PH-253, child 3/3, FINAL): the cards gained admin-gated per-repo
 * **Setup / Scan / Sync** actions (`RepoCardActions` below the metric grid). The
 * component stays PROP-DRIVEN for DATA — BoardDetail keeps owning the
 * `["board", boardKey]` query; the actions are mutations whose `onSuccess`
 * invalidates that exact key so the grid re-renders with fresh `repo_health`.
 * Mirrors the board-level `SonarSetupSection` pattern (3 mutations + invalidate +
 * inline never-silent-fail feedback) but at per-card granularity.
 *
 * One `RepoHealthCard` per `repo_health[]` entry:
 *   - header: repo_name (or "Board total" for a null-identity aggregate row) +
 *     a `primary` badge when `repo.is_primary` (PH-251 — sourced from the un-gated
 *     `repo_health[].is_primary`, NOT the member-gated /repositories-derived
 *     primarySlug which 403s for non-member viewers and silently dropped the badge).
 *   - quality-gate pill (shared GATE_MAP tones — DRY via metricMeta, Risk R4).
 *   - 6 metric tiles: bugs / vulnerabilities / code_smells / coverage /
 *     duplications / ncloc.
 *   - PH-256: admin-only Setup/Scan/Sync action row (NOT on the null aggregate row).
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
 * `.eyebrow`, `.mono`, `.btn-primary/.btn-ghost`, semantic `text-success/
 * warning/danger`, `bg-*-soft`, `text-text-*`, `bg-raised`, `border-hairline`,
 * `rounded-*`). No baked hexes, no slate/indigo — theme-aware for free.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  Boxes,
  ShieldCheck,
  RefreshCw,
  Radar,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  X,
} from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import { cn } from "@/lib/utils";
import type { RepoHealth, SonarRepoScanResult } from "@/types/api";
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

/**
 * PH-256 — map a PER-REPO scan result to an HONEST inline message + tone, keyed
 * off `job_id` first (the scannable/non-scannable split) then `scan_status`.
 * A non-null `job_id` ⇒ the scan is queued (metrics land async → Sync refreshes);
 * a null `job_id` ⇒ NON-scannable (e.g. C# in CE / unconfigured / disabled) → an
 * HONEST non-queued message, NEVER a fake queued state. `tone` ∈
 * "success" | "warning" | "danger" drives the inline region styling.
 */
function repoScanFeedback(
  result: SonarRepoScanResult,
): { tone: "success" | "warning" | "danger"; message: string } {
  // Scannable: a job was enqueued — honest async copy (metrics are NOT instant).
  if (result.job_id != null) {
    return {
      tone: "success",
      message:
        "Scan queued — analysis runs in the background; metrics appear after it completes. Sync to refresh.",
    };
  }
  // Non-scannable: NO job. Surface the honest reason; never fake a queued state.
  switch (result.scan_status) {
    case "unsupported":
      return {
        tone: "warning",
        message:
          result.message ||
          "This repo's language is not supported by SonarQube Community Edition.",
      };
    case "disabled":
      return {
        tone: "warning",
        message: "SonarQube is not enabled on this server.",
      };
    case "unconfigured":
      return {
        tone: "warning",
        message: "Run Setup to link a project key before scanning.",
      };
    case "error":
      return {
        tone: "danger",
        message: result.message || "The scan could not be started.",
      };
    default:
      return {
        tone: "warning",
        message: result.message || "This repo cannot be scanned right now.",
      };
  }
}

/**
 * Map a mutation error to a friendly message. A 403 (`require_board_admin`) →
 * "Admin role required"; any other `ApiRequestError`/`Error` → its message
 * (422 bad key / 409 dup / unreachable all surface verbatim, never silent).
 */
function errorMessage(err: Error | null | undefined): string | null {
  if (!err) return null;
  if (err instanceof ApiRequestError && err.status === 403) {
    return "Admin role required to run this action.";
  }
  return err.message || "Something went wrong.";
}

/**
 * PH-256 — admin-only per-repo action row (Setup / Scan / Sync). Rendered inside
 * `RepoHealthCard` BELOW the metric grid, ONLY when `isAdmin` AND the card has a
 * non-null `repo_slug` (the selector). The null-identity aggregate row has no
 * selector → this sub-component is never mounted there.
 *
 * Three mutations keyed off `repo.repo_slug`; every `onSuccess` invalidates the
 * SINGLE `["board", boardKey]` query (refetchType active) that drives
 * `repo_health` → the touched card re-renders in place. Per-card error/feedback
 * is an inline region (role=alert / role=status) — NO global toast dep, never a
 * silent fail, never a crash (AC-4).
 */
function RepoCardActions({
  repo,
  boardKey,
  title,
}: Readonly<{ repo: RepoHealth; boardKey: string; title: string }>) {
  const queryClient = useQueryClient();
  // The selector — guaranteed non-null by the caller's `repo.repo_slug != null`
  // gate (the aggregate row never mounts this). Narrow it once for the closures.
  const selector = repo.repo_slug as string;
  const [setupOpen, setSetupOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(repo.project_key);

  // Single key drives BoardResponse.repo_health → the touched card re-renders.
  const invalidateBoard = () =>
    queryClient.invalidateQueries({
      queryKey: ["board", boardKey],
      refetchType: "active",
    });

  const setupMutation = useMutation<RepoHealth, Error, string>({
    mutationFn: (projectKey: string) =>
      api.sonarqube.setupRepo(boardKey, selector, { project_key: projectKey }),
    onSuccess: () => {
      setSetupOpen(false);
      void invalidateBoard();
    },
  });

  const scanMutation = useMutation<SonarRepoScanResult, Error, void>({
    mutationFn: () => api.sonarqube.scanRepo(boardKey, selector),
    onSuccess: () => {
      void invalidateBoard();
    },
  });

  const syncMutation = useMutation<RepoHealth, Error, void>({
    mutationFn: () => api.sonarqube.syncRepo(boardKey, selector),
    onSuccess: () => {
      void invalidateBoard();
    },
  });

  const busy =
    setupMutation.isPending || scanMutation.isPending || syncMutation.isPending;

  // First non-null error across the three mutations → one inline alert region.
  const actionError = errorMessage(
    setupMutation.error ?? scanMutation.error ?? syncMutation.error,
  );
  // Honest scan feedback (queued vs non-scannable), keyed off job_id/scan_status.
  const scanFeedback =
    scanMutation.data && !scanMutation.error
      ? repoScanFeedback(scanMutation.data)
      : null;
  // Setup/Sync share a generic "done" line (no instant-metric claim, unlike scan).
  const genericSucceeded =
    (setupMutation.isSuccess || syncMutation.isSuccess) &&
    !setupMutation.error &&
    !syncMutation.error;

  const submitSetup = (event: React.FormEvent) => {
    event.preventDefault();
    setupMutation.mutate(keyDraft.trim());
  };

  return (
    <div
      className="flex flex-col gap-2 border-t border-hairline pt-3"
      data-testid={`repo-card-actions-${selector}`}
    >
      {/* Inline error region — 403 → "Admin role required"; 422/409/other verbatim. */}
      {actionError && (
        <div
          className="flex items-start gap-2 rounded-md bg-danger-soft px-2.5 py-2 text-xs text-danger"
          role="alert"
          data-testid={`repo-card-error-${selector}`}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{actionError}</span>
        </div>
      )}

      {/* Honest scan feedback — queued (async) vs non-scannable (no fake queued). */}
      {scanFeedback && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-md px-2.5 py-2 text-xs",
            scanFeedback.tone === "success" && "bg-success-soft text-success",
            scanFeedback.tone === "warning" && "bg-warning-soft text-warning",
            scanFeedback.tone === "danger" && "bg-danger-soft text-danger",
          )}
          role={scanFeedback.tone === "danger" ? "alert" : "status"}
          data-testid={`repo-card-scan-feedback-${selector}`}
        >
          {scanFeedback.tone === "success" ? (
            <Radar className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          )}
          <span>{scanFeedback.message}</span>
        </div>
      )}

      {/* Generic Setup/Sync success — no instant-metric claim (scan carries its own). */}
      {genericSucceeded && !scanFeedback && (
        <div
          className="flex items-center gap-2 rounded-md bg-success-soft px-2.5 py-2 text-xs text-success"
          role="status"
          data-testid={`repo-card-success-${selector}`}
        >
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>Done — metrics refreshed.</span>
        </div>
      )}

      {/* Setup disclosure form — pre-filled with the derived default project_key. */}
      {setupOpen && (
        <form
          className="flex flex-col gap-2 rounded-md bg-raised px-2.5 py-2.5"
          onSubmit={submitSetup}
          data-testid={`repo-card-setup-form-${selector}`}
        >
          <label
            className="eyebrow text-text-muted"
            htmlFor={`repo-setup-key-${selector}`}
          >
            Project key
          </label>
          <input
            id={`repo-setup-key-${selector}`}
            type="text"
            value={keyDraft}
            onChange={(event) => setKeyDraft(event.target.value)}
            className="mono w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-xs text-text-primary focus:border-accent focus:outline-none"
            data-testid={`repo-card-setup-input-${selector}`}
            autoFocus
          />
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={busy}
              className="btn-primary inline-flex items-center gap-1.5 text-xs"
              data-testid={`repo-card-setup-save-${selector}`}
            >
              {setupMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setSetupOpen(false);
                setKeyDraft(repo.project_key);
              }}
              className="btn-ghost inline-flex items-center gap-1.5 text-xs"
              data-testid={`repo-card-setup-cancel-${selector}`}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Action button row. */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setSetupOpen((open) => !open)}
          disabled={busy}
          aria-expanded={setupOpen}
          aria-label={`Setup ${title}`}
          className="btn-ghost inline-flex items-center gap-1.5 text-xs"
          data-testid={`repo-card-setup-btn-${selector}`}
        >
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          Setup
        </button>

        <button
          type="button"
          onClick={() => scanMutation.mutate()}
          disabled={busy}
          aria-label={`Scan ${title}`}
          className="btn-ghost inline-flex items-center gap-1.5 text-xs"
          data-testid={`repo-card-scan-btn-${selector}`}
        >
          {scanMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Radar className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Scan
        </button>

        <button
          type="button"
          onClick={() => syncMutation.mutate()}
          disabled={busy}
          aria-label={`Sync ${title}`}
          className="btn-ghost inline-flex items-center gap-1.5 text-xs"
          data-testid={`repo-card-sync-btn-${selector}`}
        >
          {syncMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          Sync
        </button>
      </div>
    </div>
  );
}

function RepoHealthCard({
  repo,
  isPrimary,
  isAdmin,
  boardKey,
}: Readonly<{
  repo: RepoHealth;
  isPrimary: boolean;
  isAdmin: boolean;
  boardKey: string;
}>) {
  // Null identity = the board-level aggregate row (a single-repo board with no
  // per-repo Repository row, e.g. KIM). Never crash on null — title it honestly.
  const title = repo.repo_name ?? "Board total";
  // A linked-but-never-scanned repo has a null gate → honest "no analysis yet".
  const scanned = repo.quality_gate_status != null;
  const gate = resolveGate(repo.quality_gate_status);

  const bugsTone = (repo.bugs ?? 0) > 0 ? "text-danger" : undefined;
  const vulnTone = (repo.vulnerabilities ?? 0) > 0 ? "text-danger" : undefined;

  // PH-256 — actions render on EVERY card WITH a non-null repo_slug (primary +
  // secondaries, incl. unscanned), but ONLY for admins. The null-identity
  // aggregate row (repo_slug == null) has no selector → NO actions (the
  // board-level controls in BoardSettings cover it).
  const showActions = isAdmin && repo.repo_slug != null;

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

      {/* PH-256 — admin-only per-repo Setup/Scan/Sync row (below grid, above
          footer). Suppressed for non-admins AND on the null aggregate row. */}
      {showActions && (
        <RepoCardActions repo={repo} boardKey={boardKey} title={title} />
      )}

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
  /**
   * PH-256 — board admin gate (BoardDetail computes `useBoardRole === "admin"`).
   * Admin → per-card Setup/Scan/Sync actions render; non-admin → hidden, NO
   * per-repo mutation/query fires (AC-5). Defaults false (defense-in-depth).
   */
  isAdmin?: boolean;
  /** PH-256 — the board key the per-repo action endpoints + invalidation use. */
  boardKey?: string;
}

export function SonarRepoHealthCards({
  repoHealth,
  isAdmin = false,
  boardKey = "",
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
            isAdmin={isAdmin}
            boardKey={boardKey}
          />
        ))}
      </div>
    </div>
  );
}
