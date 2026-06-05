/**
 * BranchPanel.tsx — PH-160 (G11)
 *
 * Branch detail panel rendered in the right column of the Branch Graph view.
 * Shows branch metadata (name, ahead/behind, linked ticket) + commit list (last 30)
 * + inline DiffViewer toggle ("main'e diff").
 *
 * Layout: <aside role="complementary"> with flex-col interior.
 * Responsive: w-full on mobile, w-80 on lg+.
 * State: diffOpen toggle for DiffViewer section.
 */

import { useState, useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { X, GitBranch, RefreshCw, AlertCircle, ArrowLeft } from "lucide-react";

import { api } from "@/api/client";
import { DiffViewer } from "@/components/diff";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface BranchPanelProps {
  boardKey: string;
  selectedBranch: string | null;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a relative timestamp (e.g. "3 days ago", "2 hours ago"). */
function relativeTime(isoDate: string): string {
  const date = new Date(isoDate);
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMo = Math.floor(diffDay / 30);
  if (diffMo < 12) return `${diffMo}mo ago`;
  return `${Math.floor(diffMo / 12)}y ago`;
}

// ---------------------------------------------------------------------------
// CommitListItem — inline sub-component (G12 hook: onCommitSelect no-op now)
// ---------------------------------------------------------------------------

interface CommitListItemProps {
  sha: string;
  shortSha: string;
  summary: string;
  authorName: string;
  authoredAt: string;
}

function CommitListItem({ shortSha, summary, authorName, authoredAt }: CommitListItemProps) {
  return (
    <li className="flex flex-col gap-0.5 rounded px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-700/50 cursor-default text-xs">
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="font-mono text-slate-400 dark:text-slate-500 flex-shrink-0 text-[10px]">
          {shortSha}
        </span>
        <span className="truncate text-slate-800 dark:text-slate-200 font-medium">
          {summary}
        </span>
      </div>
      <div className="flex items-center gap-1 text-[10px] text-slate-400 dark:text-slate-500">
        <span>{authorName}</span>
        <span aria-hidden="true">·</span>
        <span>{relativeTime(authoredAt)}</span>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// BranchPanel
// ---------------------------------------------------------------------------

export function BranchPanel({ boardKey, selectedBranch, onClose }: BranchPanelProps) {
  const [diffOpen, setDiffOpen] = useState(false);
  const diffRegionId = useId();

  // When branch changes, reset diff view
  const [prevBranch, setPrevBranch] = useState<string | null>(null);
  if (prevBranch !== selectedBranch) {
    setPrevBranch(selectedBranch);
    setDiffOpen(false);
  }

  // ---------------------------------------------------------------------------
  // Data — branches (shared cache key with any other consumer)
  // ---------------------------------------------------------------------------
  const branchesQuery = useQuery({
    queryKey: ["git", boardKey, "branches"],
    queryFn: () => api.git.getBranches(boardKey),
    enabled: Boolean(boardKey && selectedBranch),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const branchEntry = branchesQuery.data?.branches.find(
    (b) => b.name === selectedBranch,
  ) ?? null;

  const defaultBranchName =
    branchesQuery.data?.branches.find((b) => b.is_default)?.name ?? "main";

  const isDefaultBranch = branchEntry?.is_default ?? false;

  // ---------------------------------------------------------------------------
  // Data — commits for selected branch
  // ---------------------------------------------------------------------------
  const commitsQuery = useQuery({
    queryKey: ["git", boardKey, "commits", selectedBranch, 30],
    queryFn: () =>
      api.git.listCommits(boardKey, { branch: selectedBranch ?? undefined, limit: 30 }),
    enabled: Boolean(boardKey && selectedBranch),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  // ---------------------------------------------------------------------------
  // Empty state — no branch selected
  // ---------------------------------------------------------------------------
  if (!selectedBranch) {
    return (
      <aside
        role="complementary"
        aria-label="Branch detail panel"
        className={cn(
          "flex flex-col items-center justify-center gap-3 text-center",
          "w-full lg:w-80 lg:flex-shrink-0",
          "rounded-lg border border-slate-200 dark:border-slate-700",
          "bg-white dark:bg-slate-800 p-4",
        )}
        style={{ minHeight: 200 }}
      >
        <GitBranch className="h-8 w-8 text-slate-300 dark:text-slate-600" aria-hidden="true" />
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Sol panelden bir branch seçin
        </p>
      </aside>
    );
  }

  // ---------------------------------------------------------------------------
  // Full panel layout
  // ---------------------------------------------------------------------------
  return (
    <aside
      role="complementary"
      aria-label="Branch detail panel"
      className={cn(
        "flex flex-col gap-3",
        "w-full lg:w-80 lg:flex-shrink-0",
        "rounded-lg border border-slate-200 dark:border-slate-700",
        "bg-white dark:bg-slate-800 p-3",
        "overflow-y-auto",
      )}
      style={{ maxHeight: "calc(100vh - 200px)" }}
    >
      {/* ------------------------------------------------------------------ */}
      {/* Header                                                              */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {/* Branch name row */}
          <div className="flex items-center gap-2 min-w-0">
            <span
              className="font-semibold truncate text-slate-900 dark:text-slate-100 text-sm"
              title={selectedBranch}
            >
              {selectedBranch}
            </span>
            {isDefaultBranch && (
              <span className="flex-shrink-0 rounded bg-slate-900 dark:bg-slate-600 px-1.5 py-0.5 font-mono text-[9px] text-white">
                HEAD
              </span>
            )}
          </div>

          {/* Linked ticket link */}
          {branchEntry?.ticket_key && (
            <Link
              to={`/boards/${boardKey}/tickets/${branchEntry.ticket_key}`}
              className="mt-0.5 inline-block text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              {branchEntry.ticket_key}
            </Link>
          )}
        </div>

        {/* Close button */}
        <button
          type="button"
          aria-label="Close branch panel"
          onClick={onClose}
          className="flex-shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Ahead / Behind chip                                                 */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-center gap-1 text-xs font-mono">
        {branchesQuery.isLoading ? (
          <span className="text-slate-400">...</span>
        ) : (
          <>
            <span
              className={cn(
                "font-semibold",
                branchEntry?.ahead === null
                  ? "text-slate-400 dark:text-slate-500"
                  : "text-green-600 dark:text-green-400",
              )}
              title={
                branchEntry?.ahead === null
                  ? "Deep divergence — hesaplanamadı"
                  : `${branchEntry?.ahead ?? 0} commit ahead`
              }
            >
              ↑ {branchEntry?.ahead === null ? "..." : (branchEntry?.ahead ?? 0)}
            </span>
            <span className="text-slate-300 dark:text-slate-600" aria-hidden="true">·</span>
            <span
              className={cn(
                "font-semibold",
                branchEntry?.behind === null
                  ? "text-slate-400 dark:text-slate-500"
                  : "text-red-600 dark:text-red-400",
              )}
              title={
                branchEntry?.behind === null
                  ? "Deep divergence — hesaplanamadı"
                  : `${branchEntry?.behind ?? 0} commit behind`
              }
            >
              ↓ {branchEntry?.behind === null ? "..." : (branchEntry?.behind ?? 0)}
            </span>
          </>
        )}
        <span className="ml-auto text-[10px] text-slate-400 dark:text-slate-500">
          vs {defaultBranchName}
        </span>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Diff toggle button                                                  */}
      {/* ------------------------------------------------------------------ */}
      {!diffOpen ? (
        <button
          type="button"
          disabled={isDefaultBranch || branchesQuery.isLoading}
          aria-expanded={false}
          title={isDefaultBranch ? "Default branch — kendine diff yok" : undefined}
          onClick={() => setDiffOpen(true)}
          className={cn(
            "rounded border px-3 py-1.5 text-xs font-medium transition-colors",
            "border-slate-300 dark:border-slate-600",
            "text-slate-700 dark:text-slate-300",
            "hover:bg-slate-50 dark:hover:bg-slate-700",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500",
            "disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          {defaultBranchName}&#39;e diff
        </button>
      ) : (
        <div id={diffRegionId} className="flex flex-col gap-2">
          <button
            type="button"
            aria-expanded={true}
            aria-controls={diffRegionId}
            onClick={() => setDiffOpen(false)}
            className={cn(
              "flex items-center gap-1.5 rounded border px-3 py-1.5 text-xs font-medium transition-colors",
              "border-slate-300 dark:border-slate-600",
              "text-slate-700 dark:text-slate-300",
              "hover:bg-slate-50 dark:hover:bg-slate-700",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500",
            )}
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Geri
          </button>
          <DiffViewer
            fetch={{
              kind: "range",
              boardKey,
              base: defaultBranchName,
              head: selectedBranch,
            }}
          />
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Commit list (only shown when diff is closed)                        */}
      {/* ------------------------------------------------------------------ */}
      {!diffOpen && (
        <div className="flex flex-col gap-1">
          <h3 className="px-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            Commits (son 30)
          </h3>

          {/* Loading skeleton */}
          {commitsQuery.isLoading && (
            <div role="status" aria-live="polite" className="flex flex-col gap-1.5 px-2 py-1">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex flex-col gap-1 animate-pulse">
                  <div className="h-3 w-3/4 rounded bg-slate-200 dark:bg-slate-700" />
                  <div className="h-2.5 w-1/2 rounded bg-slate-100 dark:bg-slate-700/50" />
                </div>
              ))}
              <span className="sr-only">Commits yükleniyor…</span>
            </div>
          )}

          {/* Error state */}
          {commitsQuery.error && !commitsQuery.isLoading && (
            <div className="flex flex-col items-center gap-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
              <div className="flex items-center gap-1.5">
                <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
                <span>Commits yüklenirken hata oluştu.</span>
              </div>
              <button
                type="button"
                onClick={() => void commitsQuery.refetch()}
                className="flex items-center gap-1 rounded border border-red-300 px-2 py-1 text-[10px] hover:bg-red-100 dark:border-red-700 dark:hover:bg-red-900/30"
              >
                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                Tekrar dene
              </button>
            </div>
          )}

          {/* Empty state */}
          {!commitsQuery.isLoading &&
            !commitsQuery.error &&
            (commitsQuery.data?.commits.length ?? 0) === 0 && (
              <p className="px-2 py-2 text-xs text-slate-400 dark:text-slate-500">
                Bu branch&apos;te commit yok.
              </p>
            )}

          {/* Commit items */}
          {!commitsQuery.isLoading && (commitsQuery.data?.commits ?? []).length > 0 && (
            <ul role="list" className="flex flex-col">
              {commitsQuery.data!.commits.map((commit) => (
                <CommitListItem
                  key={commit.sha}
                  sha={commit.sha}
                  shortSha={commit.short_sha}
                  summary={commit.summary}
                  authorName={commit.author_name}
                  authoredAt={commit.authored_at}
                />
              ))}
            </ul>
          )}
        </div>
      )}
    </aside>
  );
}
