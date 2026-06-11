/**
 * RepositoryList — C5 (PH-225)
 *
 * Multi-repo list for Settings → Git. Each board may own 1:N repositories with
 * exactly one primary (PH-221). A single-repo board renders as ONE row (= the
 * primary, badge shown) — byte-equivalent UX to the legacy single-repo tab
 * (back-compat AC).
 *
 * Each row (RepositoryRow) shows:
 *   - name (+ slug as a <code> sub-label)
 *   - provider chip · remote_url (truncate, italic 'local-only' when null)
 *   - default_branch <code> · last-sync relative time
 *   - PRIMARY badge when is_primary
 *   - admin-only action cluster: Set primary (disabled when already primary),
 *     Remove (opens RemoveRepoConfirmModal), and an Operations disclosure that
 *     reveals the board-scoped RepositoryOperationsPanel (refresh / rotate-secret
 *     / hook snippet). Non-admin → read-only row, no actions.
 *
 * Writes are admin-auth (403 for non-admin): actions are HIDDEN when !isAdmin
 * (primary defence); set-primary's onError also catches 403 → inline message
 * (defensive — board-admin membership and the UI role can disagree, PH-224).
 *
 * Mutations refetch (NOT optimistic): set-primary/remove have server-side
 * primary auto-promotion the client cannot predict → the list query is the
 * source of truth. onSuccess invalidates list + detect + git status.
 *
 * A11y: <ul role="list">, semantic disclosure via <button aria-expanded>,
 * badges carry text, all action buttons have aria-labels.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Star,
  Trash2,
  ChevronDown,
  ChevronRight,
  GitBranch as GitBranchIcon,
  Loader2,
} from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import { humaniseRelativeTr } from "@/lib/time";
import type { RepositoryResponse } from "@/types/git";
import { RepositoryOperationsPanel } from "./RepositoryOperationsPanel";
import { RemoveRepoConfirmModal } from "./RemoveRepoConfirmModal";

// ---------------------------------------------------------------------------
// Provider chip
// ---------------------------------------------------------------------------

function ProviderChip({ provider }: Readonly<{ provider: string }>) {
  return (
    <span className="badge inline-flex items-center bg-inset px-2 py-0.5 text-xs text-text-secondary">
      {provider}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

interface RepositoryRowProps {
  boardKey: string;
  repo: RepositoryResponse;
  isAdmin: boolean;
  /** total repo count — drives auto-promote warning + lone-primary affordances */
  repoCount: number;
  onMutated: () => void;
}

function RepositoryRow({
  boardKey,
  repo,
  isAdmin,
  repoCount,
  onMutated,
}: Readonly<RepositoryRowProps>) {
  const [expanded, setExpanded] = useState(false);
  const [showRemove, setShowRemove] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const setPrimaryMutation = useMutation({
    mutationFn: () => api.git.setPrimaryRepository(boardKey, repo.slug),
    onSuccess: () => {
      setActionError(null);
      onMutated();
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 403) {
        setActionError("Bu işlem için admin yetkisi gerekli.");
      } else {
        setActionError(
          err instanceof Error ? err.message : "Birincil ayarlanamadı.",
        );
      }
    },
  });

  const shortSha = repo.last_synced_sha
    ? repo.last_synced_sha.slice(0, 7)
    : null;

  return (
    <li
      className="rounded-lg border border-hairline bg-surface"
      data-testid={`repo-row-${repo.slug}`}
      data-primary={repo.is_primary}
    >
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        {/* Identity + metadata */}
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-text-primary">{repo.name}</span>
            <code className="mono rounded bg-inset px-1.5 py-0.5 text-xs text-text-muted">
              {repo.slug}
            </code>
            {repo.is_primary && (
              <span
                className="badge inline-flex items-center gap-1 bg-success-soft px-2 py-0.5 text-xs font-medium text-success"
                data-testid={`primary-badge-${repo.slug}`}
              >
                <Star className="h-3 w-3 fill-current" />
                BİRİNCİL
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary">
            <ProviderChip provider={repo.provider} />
            <span className="inline-flex items-center gap-1">
              <GitBranchIcon className="h-3 w-3" />
              <code className="mono">{repo.default_branch}</code>
            </span>
            <span className="max-w-xs truncate">
              {repo.remote_url ?? (
                <span className="italic text-text-muted">local-only</span>
              )}
            </span>
            <span>
              Son senkron:{" "}
              <time dateTime={repo.last_synced_at ?? ""}>
                {humaniseRelativeTr(repo.last_synced_at)}
              </time>
            </span>
            {shortSha && (
              <code className="mono rounded bg-inset px-1.5 py-0.5">
                {shortSha}
              </code>
            )}
          </div>

          <p className="mono text-xs text-text-muted">{repo.local_path}</p>
        </div>

        {/* Actions — admin only */}
        {isAdmin && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setPrimaryMutation.mutate()}
              disabled={repo.is_primary || setPrimaryMutation.isPending}
              className="btn btn-secondary inline-flex items-center gap-1.5 py-1.5 text-xs disabled:opacity-50"
              aria-label={
                repo.is_primary
                  ? "Zaten birincil"
                  : `${repo.name} deposunu birincil yap`
              }
              title={repo.is_primary ? "Zaten birincil" : "Birincil yap"}
              data-testid={`set-primary-${repo.slug}`}
            >
              {setPrimaryMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Star className="h-3 w-3" />
              )}
              Birincil yap
            </button>

            <button
              type="button"
              onClick={() => setShowRemove(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-danger px-2.5 py-1.5 text-xs font-medium text-danger hover:bg-danger-soft"
              aria-label={`${repo.name} deposunu kaldır`}
              data-testid={`remove-${repo.slug}`}
            >
              <Trash2 className="h-3 w-3" />
              Kaldır
            </button>

            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="btn-ghost inline-flex items-center gap-1 py-1.5 text-xs"
              aria-expanded={expanded}
              aria-controls={`repo-ops-${repo.slug}`}
              aria-label={`${repo.name} işlemlerini ${expanded ? "gizle" : "göster"}`}
              data-testid={`ops-toggle-${repo.slug}`}
            >
              {expanded ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              İşlemler
            </button>
          </div>
        )}
      </div>

      {/* Inline action error (e.g. 403 on set-primary) */}
      {actionError && (
        <div
          className="mx-4 mb-3 rounded-md bg-danger-soft px-3 py-2 text-xs text-danger"
          role="alert"
          data-testid={`row-error-${repo.slug}`}
        >
          {actionError}
        </div>
      )}

      {/* Per-repo operations disclosure (board-scoped refresh/rotate/hook) */}
      {isAdmin && expanded && (
        <div
          id={`repo-ops-${repo.slug}`}
          className="border-t border-hairline p-4"
        >
          <RepositoryOperationsPanel
            boardKey={boardKey}
            connected
            localPath={repo.local_path}
          />
        </div>
      )}

      {showRemove && (
        <RemoveRepoConfirmModal
          boardKey={boardKey}
          repo={repo}
          willAutoPromote={repo.is_primary && repoCount > 1}
          onClose={() => setShowRemove(false)}
          onRemoved={onMutated}
        />
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

interface RepositoryListProps {
  boardKey: string;
  repositories: RepositoryResponse[];
  isAdmin: boolean;
  isLoading: boolean;
  isError: boolean;
}

export function RepositoryList({
  boardKey,
  repositories,
  isAdmin,
  isLoading,
  isError,
}: Readonly<RepositoryListProps>) {
  const qc = useQueryClient();

  const refetchAll = () => {
    qc.invalidateQueries({ queryKey: ["repositories", boardKey] });
    qc.invalidateQueries({ queryKey: ["repositories", boardKey, "detect"] });
    qc.invalidateQueries({ queryKey: ["git", boardKey, "status"] });
  };

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Depolar yükleniyor...</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div
        className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
        role="alert"
        data-testid="repo-list-error"
      >
        Depolar alınamadı. Lütfen tekrar deneyin.
      </div>
    );
  }

  if (repositories.length === 0) {
    return (
      <output
        className="block rounded-md bg-inset px-4 py-6 text-center text-sm text-text-muted"
        data-testid="repo-list-empty"
      >
        Bu board'a henüz bir git deposu bağlanmadı. Aşağıdan ekleyin.
      </output>
    );
  }

  return (
    <ul className="space-y-3" data-testid="repo-list">
      {repositories.map((repo) => (
        <RepositoryRow
          key={repo.id}
          boardKey={boardKey}
          repo={repo}
          isAdmin={isAdmin}
          repoCount={repositories.length}
          onMutated={refetchAll}
        />
      ))}
    </ul>
  );
}
