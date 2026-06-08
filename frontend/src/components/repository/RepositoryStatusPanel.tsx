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
import { humaniseRelativeTr } from "@/lib/time";

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
}: Readonly<RepositoryStatusPanelProps>) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
        <RefreshCw className="h-4 w-4 animate-spin" />
        <span>Durum yükleniyor...</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div
        className="flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
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
      <output
        className="badge inline-flex items-center gap-2 bg-danger-soft px-3 py-1.5 text-sm text-danger"
        data-testid="repo-status-disconnected"
      >
        <XCircle className="h-4 w-4 shrink-0" />
        <span>Repo bağlı değil. Aşağıdan bağlayın.</span>
      </output>
    );
  }

  const shortSha = repo.last_synced_sha ? repo.last_synced_sha.slice(0, 7) : null;

  return (
    <output
      className="block space-y-3"
      data-testid="repo-status-connected"
      aria-label="Repository bağlantı durumu"
    >
      <div className="badge inline-flex items-center gap-2 bg-success-soft px-3 py-1.5">
        <CheckCircle className="h-4 w-4 text-success" />
        <span className="text-sm font-medium text-success">
          Bağlı
        </span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt className="font-medium text-text-secondary">Provider</dt>
        <dd className="text-text-primary">{repo.provider}</dd>

        <dt className="font-medium text-text-secondary">Remote URL</dt>
        <dd className="break-all text-text-primary">
          {repo.remote_url ?? (
            <span className="italic text-text-muted">local-only</span>
          )}
        </dd>

        <dt className="font-medium text-text-secondary">Default branch</dt>
        <dd>
          <code className="mono rounded bg-inset px-1.5 py-0.5 text-xs text-text-secondary">
            {repo.default_branch}
          </code>
        </dd>

        <dt className="font-medium text-text-secondary">Local path</dt>
        <dd>
          <code className="mono rounded bg-inset px-1.5 py-0.5 text-xs text-text-secondary">
            {repo.local_path}
          </code>
        </dd>

        <dt className="font-medium text-text-secondary">Son senkron</dt>
        <dd className="text-text-primary">
          <time dateTime={repo.last_synced_at ?? ""}>
            {humaniseRelativeTr(repo.last_synced_at)}
          </time>
        </dd>

        {shortSha && (
          <>
            <dt className="font-medium text-text-secondary">Son commit</dt>
            <dd>
              <code className="mono rounded bg-inset px-1.5 py-0.5 font-mono text-xs text-text-secondary">
                {shortSha}
              </code>
            </dd>
          </>
        )}
      </dl>
    </output>
  );
}
