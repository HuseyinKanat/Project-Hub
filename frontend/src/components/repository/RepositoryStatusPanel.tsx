/**
 * RepositoryStatusPanel — G13 (PH-162)
 *
 * Displays the current git repository connection status for a board.
 * Shows provider, remote_url, default_branch, local_path, last_synced_at,
 * last_synced_sha (short 7-char) when connected; otherwise a "not connected" message.
 *
 * Props:
 *  - boardKey: string        — board key for query
 *  - isAdmin: boolean        — controls whether config form + ops are available (passed up)
 *
 * Query: ['git', boardKey, 'status'] → GET /api/boards/{key}/git/status
 * No writes; all mutations are in sibling panels.
 *
 * A11y: uses <dl> for key-value pairs, humanised date via Intl.RelativeTimeFormat.
 */

import { CheckCircle, XCircle, RefreshCw } from "lucide-react";
import type { GitStatus } from "@/types/git";

// ---------------------------------------------------------------------------
// Relative time helper
// ---------------------------------------------------------------------------

function humaniseRelative(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso);
  const diffMs = Date.now() - then.getTime();
  const diffSec = Math.round(diffMs / 1000);
  const rtf = new Intl.RelativeTimeFormat("tr", { numeric: "auto" });
  if (Math.abs(diffSec) < 60) return rtf.format(-diffSec, "second");
  const diffMin = Math.round(diffSec / 60);
  if (Math.abs(diffMin) < 60) return rtf.format(-diffMin, "minute");
  const diffHr = Math.round(diffMin / 60);
  if (Math.abs(diffHr) < 24) return rtf.format(-diffHr, "hour");
  const diffDay = Math.round(diffHr / 24);
  return rtf.format(-diffDay, "day");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface RepositoryStatusPanelProps {
  status: GitStatus | undefined;
  isLoading: boolean;
  isError: boolean;
}

export function RepositoryStatusPanel({
  status,
  isLoading,
  isError,
}: RepositoryStatusPanelProps) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-slate-500 dark:text-slate-400">
        <RefreshCw className="h-4 w-4 animate-spin" />
        <span>Durum yükleniyor...</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div
        className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400"
        role="alert"
      >
        <XCircle className="h-4 w-4 shrink-0" />
        <span>Durum alınamadı. Lütfen tekrar deneyin.</span>
      </div>
    );
  }

  const connected = status?.connected ?? false;
  const repo = status?.repository ?? null;

  if (!connected || !repo) {
    return (
      <div
        className="flex items-center gap-2 rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-300"
        role="status"
        data-testid="repo-status-disconnected"
      >
        <XCircle className="h-4 w-4 shrink-0" />
        <span>Repo bağlı değil. Aşağıdan bağlayın.</span>
      </div>
    );
  }

  const shortSha = repo.last_synced_sha ? repo.last_synced_sha.slice(0, 7) : null;

  return (
    <div
      className="space-y-3"
      role="status"
      data-testid="repo-status-connected"
      aria-label="Repository bağlantı durumu"
    >
      <div className="flex items-center gap-2">
        <CheckCircle className="h-4 w-4 text-emerald-500 dark:text-emerald-400" />
        <span className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
          Bağlı
        </span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt className="font-medium text-slate-600 dark:text-slate-400">Provider</dt>
        <dd className="text-slate-900 dark:text-slate-100">{repo.provider}</dd>

        <dt className="font-medium text-slate-600 dark:text-slate-400">Remote URL</dt>
        <dd className="break-all text-slate-900 dark:text-slate-100">
          {repo.remote_url ?? (
            <span className="italic text-slate-400 dark:text-slate-500">local-only</span>
          )}
        </dd>

        <dt className="font-medium text-slate-600 dark:text-slate-400">Default branch</dt>
        <dd>
          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs dark:bg-slate-700 dark:text-slate-300">
            {repo.default_branch}
          </code>
        </dd>

        <dt className="font-medium text-slate-600 dark:text-slate-400">Local path</dt>
        <dd>
          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs dark:bg-slate-700 dark:text-slate-300">
            {repo.local_path}
          </code>
        </dd>

        <dt className="font-medium text-slate-600 dark:text-slate-400">Son senkron</dt>
        <dd className="text-slate-900 dark:text-slate-100">
          <time dateTime={repo.last_synced_at ?? ""}>
            {humaniseRelative(repo.last_synced_at)}
          </time>
        </dd>

        {shortSha && (
          <>
            <dt className="font-medium text-slate-600 dark:text-slate-400">Son commit</dt>
            <dd>
              <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs dark:bg-slate-700 dark:text-slate-300">
                {shortSha}
              </code>
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}
