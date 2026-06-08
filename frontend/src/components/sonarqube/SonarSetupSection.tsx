/**
 * SonarSetupSection.tsx — PH-226 (epic PH-220, CHILD C6): the SonarQube
 * board-settings section rendered inside the BoardSettings `sonarqube` tab.
 *
 * Owns a single status query (`['board', boardKey, 'sonar-setup']`, distinct
 * from BoardDetail's `['board', boardKey]` BoardResponse query so the two never
 * collide — Risk R3) plus THREE admin mutations (Setup + Sync + Scan). The Sync
 * success handler invalidates THREE families so the board-detail SonarHealthPanel
 * tile refreshes without a reload (same keys BoardDetail's `sonarqube_synced` WS
 * handler hits — Risk R2): the status query, `['board', boardKey]`
 * (BoardResponse.health), and the `['board', boardKey, 'sonar-issues', ...]`
 * live-counts family.
 *
 * PH-237 — "Scan now": a SECOND, behaviorally DISTINCT action next to "Sync now".
 * Sync = cheap re-poll of the EXISTING analysis; Scan = enqueue a NEW (expensive,
 * HOST-side, ASYNC) analysis run. A lazy, admin-only scan-plan query
 * (`['board', boardKey, 'sonar-scan-plan']`) resolves the detected language +
 * whether SonarQube Community Edition can analyze the board, so the Scan button
 * is disabled/annotated with the honest `reason` (e.g. "C# / Unity not supported
 * by Community Edition") BEFORE the click. On click the six-value `scan_status`
 * (queued|running|unsupported|disabled|unconfigured|error) maps to HONEST inline
 * copy — `queued`/`running` says metrics land AFTER the background run (re-sync to
 * refresh), never a fake "status refreshed". Scan success invalidates the same
 * three families PLUS the scan-plan key.
 *
 * Admin gating mirrors the repository tab: a non-admin still SEES the status
 * panel (GET status is member-level) but gets a read-only banner and no
 * write buttons. The dev/frontend token lacks board admin → Setup/Sync return
 * 403; the mutation `onError` catches `ApiRequestError.status === 403` and shows
 * an inline "Admin role required" message — the UI never crashes (Risk R1).
 *
 * Design system: Cyan-on-Black ProjectHub tokens only (`.card`, `.badge`,
 * `.btn-primary/.btn-ghost`, semantic `text-success/warning/danger`,
 * `bg-*-soft`, `border-hairline*`, `mono`, `eyebrow`). No baked hexes — theme
 * aware for free (matches SonarHealthPanel).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ShieldCheck,
  RefreshCw,
  ExternalLink,
  Lock,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  PlugZap,
  Info,
  Radar,
} from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import { cn } from "@/lib/utils";
import type {
  SonarScanResult,
  SonarSetupStatus,
} from "@/types/api";

/**
 * Quality-gate pill descriptor keyed by the raw `quality_gate_status` value.
 * Mirrors SonarHealthPanel.GATE_MAP for visual parity; unknown / null → muted.
 */
const GATE_MAP: Record<string, { label: string; tone: string }> = {
  OK: { label: "Passed", tone: "text-success bg-success-soft" },
  ERROR: { label: "Failed", tone: "text-danger bg-danger-soft" },
  WARN: { label: "Warning", tone: "text-warning bg-warning-soft" },
};
const GATE_UNKNOWN = { label: "Unknown", tone: "text-text-muted bg-raised" };

/**
 * PH-237 — map a scan result's six-value `scan_status` to an HONEST inline
 * message + tone. `queued`/`running` deliberately do NOT claim metrics are
 * present (the scan runs HOST-side, async); `unsupported`/`error` surface the
 * backend's own `message` (the honest reason), and the rest get a fixed line.
 * `tone` ∈ "success" | "warning" | "danger" drives the inline region styling.
 */
function scanResultFeedback(
  result: SonarScanResult,
): { tone: "success" | "warning" | "danger"; message: string } {
  switch (result.scan_status) {
    case "queued":
    case "running":
      return {
        tone: "success",
        message:
          "Scan queued — analysis runs in the background; metrics appear after it completes. Re-sync to refresh.",
      };
    case "unsupported":
      // Honest CE gate — show the backend's reason verbatim (e.g. C#/Unity).
      return {
        tone: "warning",
        message:
          result.message ||
          "This board's language is not supported by SonarQube Community Edition.",
      };
    case "disabled":
      return { tone: "warning", message: "SonarQube is not enabled on this server." };
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
      // Unknown future value (LooseString) — graceful, never a blank state.
      return {
        tone: "warning",
        message: result.message || `Scan returned an unexpected status.`,
      };
  }
}

/**
 * Relative "3m ago" timestamp from an ISO string (no new dep). Falls back to a
 * locale string for unparseable input, em-dash for null.
 */
function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const sec = Math.round((Date.now() - then) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (sec < 60) return rtf.format(-sec, "second");
  if (sec < 3600) return rtf.format(-Math.round(sec / 60), "minute");
  if (sec < 86400) return rtf.format(-Math.round(sec / 3600), "hour");
  return rtf.format(-Math.round(sec / 86400), "day");
}

/** A boolean status chip (enabled / reachable / configured). */
function StatusChip({
  label,
  on,
}: Readonly<{ label: string; on: boolean }>) {
  return (
    <span
      className={cn(
        "badge gap-1.5 border border-current/30 text-xs font-medium",
        on ? "text-success bg-success-soft" : "text-text-muted bg-raised",
      )}
      data-testid={`sonar-chip-${label.toLowerCase()}`}
    >
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 rounded-full bg-current"
      />
      {label}
    </span>
  );
}

export function SonarSetupSection({
  boardKey,
  isAdmin,
  enabled,
}: Readonly<{
  boardKey: string;
  /** Board admin → may run Setup/Sync. Non-admin sees a read-only banner. */
  isAdmin: boolean;
  /** Whether this tab is active — gates the status query (lazy fetch). */
  enabled: boolean;
}>) {
  const queryClient = useQueryClient();

  // Status query — dedicated key so it never collides with BoardDetail's
  // ['board', boardKey] BoardResponse query (Risk R3). Member-level GET.
  const statusQuery = useQuery({
    queryKey: ["board", boardKey, "sonar-setup"],
    queryFn: () => api.sonarqube.getStatus(boardKey),
    enabled: Boolean(boardKey) && enabled,
    staleTime: 15_000,
  });

  // Setup mutation — one-click empty body → backend derives `project-hub` for PH.
  const setupMutation = useMutation<SonarSetupStatus, Error, void>({
    mutationFn: () => api.sonarqube.setup(boardKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey, "sonar-setup"],
      });
    },
  });

  // Sync mutation — re-poll metrics. On success, invalidate THREE families so a
  // board-detail tab sees the SonarHealthPanel tile refresh (Risk R2): the
  // status query, ['board', boardKey] (BoardResponse.health), and the
  // ['board', boardKey, 'sonar-issues', ...] live-counts family (predicate
  // mirrors BoardDetail's sonarqube_synced WS handler).
  const syncMutation = useMutation<SonarSetupStatus, Error, void>({
    mutationFn: () => api.sonarqube.sync(boardKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey, "sonar-setup"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey],
        refetchType: "active",
      });
      void queryClient.invalidateQueries({
        predicate: (q) => {
          const k = q.queryKey;
          return (
            Array.isArray(k) &&
            k[0] === "board" &&
            k[1] === boardKey &&
            k[2] === "sonar-issues"
          );
        },
        refetchType: "active",
      });
    },
  });

  // PH-237 — scan-plan query: lazy AND admin-only. scan-plan is admin-gated
  // (403 for non-admin) so it MUST NOT fire for non-admins (would 403-spam,
  // Risk R1). Feeds the up-front language + supported annotation so an
  // unsupported board (e.g. C#/Unity in CE) is honest BEFORE the click.
  const scanPlanQuery = useQuery({
    queryKey: ["board", boardKey, "sonar-scan-plan"],
    queryFn: () => api.sonarqube.getScanPlan(boardKey),
    enabled: Boolean(boardKey) && enabled && isAdmin,
    staleTime: 60_000,
    retry: false,
  });

  // PH-237 — scan mutation: ENQUEUE a NEW (async, HOST-side) analysis run. On
  // success invalidate the SAME three families Sync does PLUS the scan-plan key
  // (re-resolve language/support). The metric will NOT be present immediately —
  // that is the honest async behavior, not a bug (Risk R6: no progress polling).
  const scanMutation = useMutation<SonarScanResult, Error, void>({
    mutationFn: () => api.sonarqube.scan(boardKey),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey, "sonar-setup"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey],
        refetchType: "active",
      });
      void queryClient.invalidateQueries({
        predicate: (q) => {
          const k = q.queryKey;
          return (
            Array.isArray(k) &&
            k[0] === "board" &&
            k[1] === boardKey &&
            k[2] === "sonar-issues"
          );
        },
        refetchType: "active",
      });
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey, "sonar-scan-plan"],
      });
    },
  });

  // Surface a 403 from any mutation as a friendly "Admin role required" line;
  // any other error shows its message. NEVER an unhandled rejection (Risk R1).
  // Scan mutation errors fold in here too (a thrown 403 if isAdmin is somehow
  // true but the token lacks admin) — its scan_status branches handle the
  // graceful never-500 outcomes, this is the hard-error safety net.
  const mutationError =
    setupMutation.error ?? syncMutation.error ?? scanMutation.error;
  const actionError = errorMessage(mutationError);
  // Setup/Sync share the generic "status refreshed" success line. Scan does NOT
  // — its honest async/unsupported copy comes from scanResultFeedback below, so
  // a scan result is excluded from this generic-success flag.
  const genericSucceeded =
    (setupMutation.isSuccess || syncMutation.isSuccess) && !mutationError;
  const scanFeedback =
    scanMutation.data && !scanMutation.error
      ? scanResultFeedback(scanMutation.data)
      : null;

  if (statusQuery.isLoading) {
    return (
      <div
        className="card flex items-center gap-2 p-6 text-sm text-text-muted"
        data-testid="sonar-status-loading"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading SonarQube status…
      </div>
    );
  }

  if (statusQuery.isError || !statusQuery.data) {
    return (
      <div
        className="card flex items-center gap-2 border-danger/40 bg-danger-soft p-6 text-sm text-danger"
        role="alert"
        data-testid="sonar-status-error"
      >
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
        Failed to load SonarQube status.
        <button
          type="button"
          className="btn-ghost ml-2 text-xs"
          onClick={() => void statusQuery.refetch()}
        >
          Retry
        </button>
      </div>
    );
  }

  const status = statusQuery.data;
  const gate =
    status.quality_gate_status != null
      ? (GATE_MAP[status.quality_gate_status] ?? GATE_UNKNOWN)
      : GATE_UNKNOWN;
  const busy =
    setupMutation.isPending ||
    syncMutation.isPending ||
    scanMutation.isPending;

  // Setup is primary (glowing) only when not yet linked; once configured it
  // becomes a secondary "re-affirm" action. Both are disabled when the server
  // kill switch is off (`!enabled`). Sync stays enabled even when unreachable so
  // the admin can retry; only disabled by !enabled or a pending mutation.
  const setupDisabled = !status.enabled || busy;
  const syncDisabled = !status.enabled || busy;

  // PH-237 — Scan now: enqueues a new analysis. Disabled when the server kill
  // switch is off, no project key yet (unconfigured), a mutation is pending, OR
  // the scan-plan says this board's language is NOT supported by CE
  // (supported === false) — honest up-front rather than only on click.
  const plan = scanPlanQuery.data;
  const scanUnsupported = plan?.supported === false;
  const scanDisabled =
    !status.enabled || !status.configured || busy || scanUnsupported;
  // The honest reason to annotate the disabled Scan button (from scan-plan).
  const scanUnsupportedReason =
    scanUnsupported && plan?.reason
      ? plan.reason
      : "This board's language is not supported by SonarQube Community Edition.";

  return (
    <div className="space-y-6" data-testid="sonar-setup-section">
      {/* Non-admin read-only banner (mirrors the repository tab). */}
      {!isAdmin && (
        <div
          className="flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
          role="note"
          data-testid="sonarqube-readonly-banner"
        >
          <Lock className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>Read-only — admin role required to run Setup or Sync.</span>
        </div>
      )}

      {/* enabled=false: server kill switch off. */}
      {!status.enabled && (
        <div
          className="flex items-center gap-2 rounded-md bg-raised px-4 py-3 text-sm text-text-secondary"
          role="note"
          data-testid="sonar-disabled-banner"
        >
          <PlugZap className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>SonarQube is not enabled on this server.</span>
        </div>
      )}

      {/* PH-235: the yellow unreachable banner shows ONLY for a GENUINE outage
          (status==="unreachable") — never for a configured-but-unscanned board. */}
      {status.status === "unreachable" && (
        <div
          className="flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
          role="note"
          data-testid="sonar-unreachable-banner"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            SonarQube server is unreachable or the last poll is stale. Sync to
            retry.
          </span>
        </div>
      )}

      {/* PH-235: configured but never scanned → a NEUTRAL note (NOT a false
          "unreachable" warning). The server is up; the board just has no analysis. */}
      {status.status === "no_analysis" && (
        <div
          className="flex items-center gap-2 rounded-md bg-raised px-4 py-3 text-sm text-text-secondary"
          role="note"
          data-testid="sonar-no-analysis-banner"
        >
          <Info className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>No analysis yet — run Sync (or a scan) to fetch metrics.</span>
        </div>
      )}

      {/* Status panel card. */}
      <section
        className="card space-y-4 p-6"
        data-testid="sonar-status-panel"
        aria-label="SonarQube status"
      >
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 className="text-lg font-semibold text-text-primary">
            SonarQube
          </h2>
          <span
            className={cn(
              "badge gap-1.5 border border-current/30 text-xs font-medium",
              gate.tone,
            )}
            data-testid="sonar-quality-gate"
            aria-label={`Quality gate: ${gate.label}`}
          >
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 rounded-full bg-current"
            />
            {gate.label}
          </span>
        </div>

        <p className="text-sm text-text-secondary" data-testid="sonar-message">
          {status.message}
        </p>

        {/* Flag chips. PH-235: the "Reachable" chip is HONEST — it renders only
            when reachability is actually known (status ok | unreachable). For a
            configured-but-unscanned board (no_analysis) we suppress it rather than
            show a false "Reachable off"; "No analysis" conveys the real state. */}
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip label="Enabled" on={status.enabled} />
          {(status.status === "ok" || status.status === "unreachable") && (
            <StatusChip label="Reachable" on={status.reachable} />
          )}
          {status.status === "no_analysis" && (
            <span
              className="badge gap-1.5 border border-current/30 text-xs font-medium text-text-muted bg-raised"
              data-testid="sonar-chip-no-analysis"
            >
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
              No analysis
            </span>
          )}
          <StatusChip label="Configured" on={status.configured} />
        </div>

        {/* Detail grid. */}
        <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <div className="flex flex-col gap-0.5">
            <dt className="eyebrow text-text-muted">Project key</dt>
            <dd className="mono text-text-primary" data-testid="sonar-project-key">
              {status.project_key ?? "Not linked yet"}
            </dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="eyebrow text-text-muted">Last fetched</dt>
            <dd
              className="text-text-primary"
              data-testid="sonar-last-fetched"
              title={
                status.last_metric_fetched_at
                  ? new Date(status.last_metric_fetched_at).toLocaleString()
                  : undefined
              }
            >
              {relativeTime(status.last_metric_fetched_at)}
            </dd>
          </div>

          {/* PH-237 — scan-plan language + scannable indicator. Admin-only
              (the plan query is gated on isAdmin) so this whole row is guarded
              on `plan`; a non-admin never has the data and never sees it. Makes
              support INFORMATIVE before the user clicks Scan now. */}
          {plan && (
            <div className="flex flex-col gap-1 sm:col-span-2">
              <dt className="eyebrow text-text-muted">Language</dt>
              <dd
                className="flex flex-wrap items-center gap-2 text-text-primary"
                data-testid="sonar-scan-language"
              >
                <span className="mono">{plan.language ?? "unknown"}</span>
                <span
                  className={cn(
                    "badge gap-1.5 border border-current/30 text-xs font-medium",
                    plan.supported
                      ? "text-success bg-success-soft"
                      : "text-warning bg-warning-soft",
                  )}
                  data-testid="sonar-scan-supported-chip"
                  title={plan.supported ? undefined : plan.reason}
                >
                  <span
                    aria-hidden="true"
                    className="h-1.5 w-1.5 rounded-full bg-current"
                  />
                  {plan.supported
                    ? "Scannable"
                    : "Not scannable in Community Edition"}
                </span>
              </dd>
              {!plan.supported && plan.reason && (
                <p
                  className="text-xs text-text-muted"
                  data-testid="sonar-scan-unsupported-reason"
                >
                  {plan.reason}
                </p>
              )}
            </div>
          )}
        </dl>

        {/* Open dashboard — omitted when dashboard_url is null. */}
        {status.dashboard_url && (
          <a
            href={status.dashboard_url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-ghost inline-flex items-center gap-2 text-sm"
            data-testid="sonar-dashboard-link"
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
            Open dashboard
          </a>
        )}
      </section>

      {/* Action buttons + feedback — admin only. */}
      {isAdmin && (
        <section className="card space-y-3 p-6" data-testid="sonar-actions">
          <h3 className="text-sm font-semibold text-text-primary">Actions</h3>

          {/* Inline error region (403 → "Admin role required"). */}
          {actionError && (
            <div
              className="flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
              role="alert"
              data-testid="sonar-action-error"
            >
              <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{actionError}</span>
            </div>
          )}

          {/* Generic success region — Setup/Sync only (no toast dep). Scan does
              NOT use this: a "status refreshed" line would LIE about instant
              metrics (Risk R3); the scan region below carries the honest copy. */}
          {genericSucceeded && (
            <div
              className="flex items-center gap-2 rounded-md bg-success-soft px-3 py-2 text-sm text-success"
              role="status"
              data-testid="sonar-action-success"
            >
              <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>Done — status refreshed.</span>
            </div>
          )}

          {/* PH-237 — scan result region. HONEST async/unsupported copy keyed off
              the six-value scan_status; queued/running NEVER claims metrics exist. */}
          {scanFeedback && (
            <div
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
                scanFeedback.tone === "success" &&
                  "bg-success-soft text-success",
                scanFeedback.tone === "warning" &&
                  "bg-warning-soft text-warning",
                scanFeedback.tone === "danger" && "bg-danger-soft text-danger",
              )}
              role={scanFeedback.tone === "danger" ? "alert" : "status"}
              data-testid="sonar-scan-feedback"
            >
              {scanFeedback.tone === "success" ? (
                <Radar className="h-4 w-4 shrink-0" aria-hidden="true" />
              ) : (
                <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
              )}
              <span>{scanFeedback.message}</span>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setupMutation.mutate()}
              disabled={setupDisabled}
              className={cn(
                "inline-flex items-center gap-2 text-sm",
                status.configured ? "btn-ghost" : "btn-primary",
              )}
              data-testid="sonar-setup-btn"
            >
              {setupMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              )}
              {status.configured ? "Re-affirm setup" : "Setup"}
            </button>

            <button
              type="button"
              onClick={() => syncMutation.mutate()}
              disabled={syncDisabled}
              className="btn-primary inline-flex items-center gap-2 text-sm"
              data-testid="sonar-sync-btn"
            >
              {syncMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              Sync now
            </button>

            {/* PH-237 — Scan now: visually + behaviorally DISTINCT from Sync.
                Radar icon (NOT RefreshCw) + a helper line "real analysis (slower)".
                Disabled/annotated up-front when scan-plan says unsupported. */}
            <button
              type="button"
              onClick={() => scanMutation.mutate()}
              disabled={scanDisabled}
              className="btn-ghost inline-flex items-center gap-2 text-sm"
              data-testid="sonar-scan-btn"
              title={scanUnsupported ? scanUnsupportedReason : undefined}
              aria-describedby="sonar-scan-help"
            >
              {scanMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Radar className="h-4 w-4" aria-hidden="true" />
              )}
              Scan now
            </button>
          </div>

          {/* Helper copy clarifying the Sync ↔ Scan distinction (AC). */}
          <p
            id="sonar-scan-help"
            className="text-xs text-text-muted"
            data-testid="sonar-scan-help"
          >
            <span className="font-medium text-text-secondary">Sync now</span>{" "}
            refreshes the existing metrics (cheap re-poll).{" "}
            <span className="font-medium text-text-secondary">Scan now</span>{" "}
            runs a new analysis on the host — it is slower and runs in the
            background; metrics appear after it completes.
            {scanUnsupported && (
              <>
                {" "}
                <span className="text-warning" data-testid="sonar-scan-disabled-note">
                  Disabled: {scanUnsupportedReason}
                </span>
              </>
            )}
          </p>
        </section>
      )}
    </div>
  );
}

/**
 * Map a mutation error to a friendly message. A 403 (`require_board_admin`) →
 * "Admin role required"; any other `ApiRequestError`/`Error` → its message.
 * Returns null when there is no error.
 */
function errorMessage(err: Error | null | undefined): string | null {
  if (!err) return null;
  if (err instanceof ApiRequestError && err.status === 403) {
    return "Admin role required to run this action.";
  }
  return err.message || "Something went wrong.";
}
