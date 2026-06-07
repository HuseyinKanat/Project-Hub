/**
 * SonarIssueDrawer — PH-204 (epic PH-201, D2): right-side overlay drawer that
 * lists a SonarQube issue type's open issues (Bugs / Vulnerabilities / Code
 * Smells), fetched lazily via `useSonarIssues`.
 *
 * REUSES the app's hand-rolled overlay-modal pattern (canonical:
 * components/repository/DetachConfirmModal.tsx) — NO new modal lib, NO shadcn
 * `ui/` dir (none exists). Same contract: `fixed inset-0 z-50` scrim with
 * `var(--bg-overlay)`, inner `.card` with `--hairline-cyan` + `--shadow-glass`
 * + backdrop-blur, `role="dialog" aria-modal="true" aria-labelledby`, an X-close
 * `<button aria-label>`, an ESC `keydown` listener, and backdrop-click-to-close
 * with inner `onClick={(e)=>e.stopPropagation()}`. Styled as an inset-y right
 * drawer (`inset-y-0 right-0 w-full max-w-xl`) instead of centered.
 *
 * A11y: mounts focus on the close button; the parent (SonarHealthPanel) restores
 * focus to the triggering tile in its `onClose`. ESC and X close the drawer.
 *
 * Body has 4 mutually-exclusive states (loading / error / empty / list); the
 * PH-203 endpoint is graceful-200 so `data.status !== 'ok'` is an error reason,
 * NOT a thrown error (Risk R3). Pagination footer shows only when
 * `total > page_size` (Risk: nice-to-have; PH live = 35 bugs < 50).
 *
 * Design system: Cyan-on-Black ProjectHub tokens only — semantic severity tones
 * (no baked hexes, Risk R5), theme-aware (dark + `html.light`) for free.
 */
import { useEffect, useRef, useState } from "react";
import {
  X,
  Bug,
  ShieldAlert,
  Sparkles,
  ExternalLink,
  Loader2,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSonarIssues } from "@/hooks/useSonarIssues";
import { ApiRequestError } from "@/api/client";
import type { SonarIssueType } from "@/types/api";

interface SonarIssueDrawerProps {
  boardKey: string;
  /** The issue type to drill into. The parent only mounts this when non-null. */
  type: SonarIssueType;
  onClose: () => void;
}

/** Type → header label + lucide icon. */
const TYPE_META: Record<
  SonarIssueType,
  { label: string; Icon: typeof Bug; empty: string }
> = {
  BUG: { label: "Bugs", Icon: Bug, empty: "No open bugs" },
  VULNERABILITY: {
    label: "Vulnerabilities",
    Icon: ShieldAlert,
    empty: "No open vulnerabilities",
  },
  CODE_SMELL: { label: "Code Smells", Icon: Sparkles, empty: "No open code smells" },
};

/**
 * Severity → semantic tone (Cyan-on-Black tokens only, no baked hexes).
 * BLOCKER/CRITICAL→danger, MAJOR→warning, MINOR→muted, INFO→info.
 * Unknown severity falls back to the muted tone.
 */
const SEVERITY_TONE: Record<string, string> = {
  BLOCKER: "text-danger bg-danger-soft",
  CRITICAL: "text-danger bg-danger-soft",
  MAJOR: "text-warning bg-warning-soft",
  MINOR: "text-text-muted bg-raised",
  INFO: "text-info bg-info-soft",
};
const SEVERITY_FALLBACK = "text-text-muted bg-raised";

/** Human reason for a non-`ok` graceful-200 status (Risk R3). */
const STATUS_REASON: Record<string, string> = {
  unreachable: "SonarQube unreachable",
  not_configured: "SonarQube not configured for this board",
  no_project_key: "No SonarQube project key for this board",
};

export function SonarIssueDrawer({
  boardKey,
  type,
  onClose,
}: Readonly<SonarIssueDrawerProps>) {
  const [page, setPage] = useState(1);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  const { data, isLoading, isError, error } = useSonarIssues(
    boardKey,
    type,
    page,
  );

  const meta = TYPE_META[type];
  const { Icon } = meta;

  // ESC closes — mirrors DetachConfirmModal.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Mount focus on the close button (parent restores focus to the tile on close).
  useEffect(() => {
    closeBtnRef.current?.focus();
  }, []);

  // Derive the body state. Graceful-200: status !== 'ok' is an error REASON,
  // not a thrown error (Risk R3). A real network failure → isError.
  const status = data?.status;
  const total = data?.total ?? 0;
  const pageSize = data?.page_size ?? 0;
  const issues = data?.issues ?? [];
  const dashboardUrl = data?.dashboard_url ?? null;

  const statusError = data != null && status !== "ok";
  const networkError = isError;
  const showError = statusError || networkError;
  const showEmpty = !showError && status === "ok" && total === 0;
  const showList = !showError && status === "ok" && total > 0;
  const showPagination = showList && total > pageSize && pageSize > 0;
  const totalPages = pageSize > 0 ? Math.ceil(total / pageSize) : 1;

  // Header count: live total once loaded, else nothing (loading).
  const headerCount = data != null && status === "ok" ? ` · ${total}` : "";

  let errorReason = "Could not load issues";
  if (statusError && typeof status === "string") {
    errorReason = STATUS_REASON[status] ?? `SonarQube error: ${status}`;
  } else if (networkError) {
    errorReason =
      error instanceof ApiRequestError || error instanceof Error
        ? error.message
        : "Could not load issues";
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      style={{ background: "var(--bg-overlay)" }}
      data-testid="sonar-issue-drawer"
    >
      {/* Dismiss surface — native button keeps the click-outside affordance
          keyboard-operable without a handler on a non-interactive element
          (SonarQube S6847/S6848). */}
      <button
        type="button"
        aria-label="Close drawer"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />
      <div
        className="card relative z-10 flex h-full w-full max-w-xl flex-col gap-0 rounded-none p-0"
        style={{
          background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--hairline-cyan)",
          boxShadow: "var(--shadow-glass)",
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="sonar-drawer-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-hairline px-5 py-4">
          <h2
            id="sonar-drawer-title"
            className="flex items-center gap-2 text-base font-semibold text-text-primary"
          >
            <Icon className="h-5 w-5 text-accent-strong" aria-hidden="true" />
            <span>
              {meta.label}
              <span className="mono text-text-muted">{headerCount}</span>
            </span>
          </h2>
          <div className="flex items-center gap-1.5">
            {dashboardUrl && (
              <a
                href={dashboardUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="badge gap-1.5 border border-current/30 text-xs font-medium text-info bg-info-soft hover:opacity-90"
                data-testid="sonar-drawer-dashboard-link"
              >
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                View in SonarQube
              </a>
            )}
            <button
              ref={closeBtnRef}
              type="button"
              onClick={onClose}
              className="rounded p-1 hover:bg-raised"
              aria-label="Close issue drawer"
              data-testid="sonar-drawer-close"
            >
              <X className="h-5 w-5 text-text-muted" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading && (
            <output
              className="flex items-center justify-center gap-2 py-12 text-sm text-text-muted"
              aria-live="polite"
              data-testid="sonar-drawer-loading"
            >
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading issues…
            </output>
          )}

          {!isLoading && showError && (
            <div
              className="flex items-start gap-3 rounded-md bg-danger-soft px-4 py-3 text-sm text-danger"
              role="alert"
              data-testid="sonar-drawer-error"
            >
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <div className="space-y-0.5">
                <p className="font-medium">{errorReason}</p>
                <p className="text-xs opacity-80">
                  Issues could not be loaded. Try again once SonarQube is
                  reachable.
                </p>
              </div>
            </div>
          )}

          {!isLoading && showEmpty && (
            <output
              className="flex flex-col items-center justify-center gap-1 py-12 text-center text-sm text-text-muted"
              data-testid="sonar-drawer-empty"
            >
              <span className="text-2xl" aria-hidden="true">
                🎉
              </span>
              <span>{meta.empty}</span>
            </output>
          )}

          {!isLoading && showList && (
            <ul className="space-y-2" data-testid="sonar-drawer-list">
              {issues.map((issue) => {
                const tone =
                  SEVERITY_TONE[issue.severity] ?? SEVERITY_FALLBACK;
                const loc =
                  issue.line != null
                    ? `${issue.component}:${issue.line}`
                    : issue.component;
                return (
                  <li
                    key={issue.key}
                    className="rounded-md bg-raised px-3 py-2.5"
                    data-testid="sonar-drawer-row"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="mono truncate text-xs text-text-secondary">
                        {loc}
                      </span>
                      <span
                        className={cn(
                          "badge shrink-0 text-2xs font-semibold uppercase",
                          tone,
                        )}
                        aria-label={`Severity: ${issue.severity}`}
                      >
                        {issue.severity}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-text-primary">
                      {issue.message}
                    </p>
                    <p className="mono mt-0.5 text-2xs text-text-muted">
                      {issue.rule}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Pagination footer — only when total > page_size */}
        {showPagination && (
          <div
            className="flex items-center justify-between gap-2 border-t border-hairline px-5 py-3"
            data-testid="sonar-drawer-pagination"
          >
            <span className="text-xs text-text-muted">
              Page {page} of {totalPages}
            </span>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                className="btn-ghost flex items-center gap-1 text-xs disabled:opacity-40"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                aria-label="Previous page"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                Prev
              </button>
              <button
                type="button"
                className="btn-ghost flex items-center gap-1 text-xs disabled:opacity-40"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                aria-label="Next page"
              >
                Next
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
