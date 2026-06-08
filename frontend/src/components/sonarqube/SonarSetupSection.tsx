/**
 * SonarSetupSection.tsx — PH-226 (epic PH-220, CHILD C6): the SonarQube
 * board-settings section rendered inside the BoardSettings `sonarqube` tab.
 *
 * Owns a single status query (`['board', boardKey, 'sonar-setup']`, distinct
 * from BoardDetail's `['board', boardKey]` BoardResponse query so the two never
 * collide — Risk R3) plus two admin mutations (Setup + Sync). The Sync success
 * handler invalidates THREE families so the board-detail SonarHealthPanel tile
 * refreshes without a reload (same keys BoardDetail's `sonarqube_synced` WS
 * handler hits — Risk R2): the status query, `['board', boardKey]`
 * (BoardResponse.health), and the `['board', boardKey, 'sonar-issues', ...]`
 * live-counts family.
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
} from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import { cn } from "@/lib/utils";
import type { SonarSetupStatus } from "@/types/api";

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

  // Surface a 403 from either mutation as a friendly "Admin role required" line;
  // any other error shows its message. NEVER an unhandled rejection (Risk R1).
  const mutationError = setupMutation.error ?? syncMutation.error;
  const actionError = errorMessage(mutationError);
  const actionSucceeded =
    (setupMutation.isSuccess || syncMutation.isSuccess) && !mutationError;

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
  const busy = setupMutation.isPending || syncMutation.isPending;

  // Setup is primary (glowing) only when not yet linked; once configured it
  // becomes a secondary "re-affirm" action. Both are disabled when the server
  // kill switch is off (`!enabled`). Sync stays enabled even when unreachable so
  // the admin can retry; only disabled by !enabled or a pending mutation.
  const setupDisabled = !status.enabled || busy;
  const syncDisabled = !status.enabled || busy;

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

      {/* reachable=false: stale / unreachable note (Sync stays enabled). */}
      {status.enabled && !status.reachable && (
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

        {/* Flag chips. */}
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip label="Enabled" on={status.enabled} />
          <StatusChip label="Reachable" on={status.reachable} />
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

          {/* Inline success region (no toast dep). */}
          {actionSucceeded && (
            <div
              className="flex items-center gap-2 rounded-md bg-success-soft px-3 py-2 text-sm text-success"
              role="status"
              data-testid="sonar-action-success"
            >
              <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>Done — status refreshed.</span>
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
          </div>
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
