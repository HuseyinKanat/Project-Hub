/**
 * TicketCommits.tsx — commit list for a ticket's Git tab.
 * G12 / PH-161
 *
 * Features:
 *   - Fetches commits via api.git.getTicketCommits(ticketKey)
 *   - Each commit row: short_sha · summary · author · time · +N/-M · files_changed count
 *   - Click row to expand → CommitFiles (numstat, per-file change_type, +/-)
 *   - Click file → DiffViewer inline (kind:'commit', boardKey, sha, path)
 *   - Commit banner over DiffViewer ("Commit: <short_sha> · <summary>")
 *   - 409 RepoNotConfigured → special UX message
 *   - Loading: 3-row skeleton; Error: red banner + retry
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, GitCommit } from "lucide-react";

import { api, ApiRequestError } from "@/api/client";
import type { GitCommitDetail } from "@/types/git";
import { cn } from "@/lib/utils";
import { DiffViewer } from "@/components/diff/DiffViewer";

// ---------------------------------------------------------------------------
// Relative time helper
// ---------------------------------------------------------------------------

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

// ---------------------------------------------------------------------------
// changeTypeBadge — mirrors FileDiffView.tsx (DRY via local copy; avoids breaking that module's encapsulation)
// ---------------------------------------------------------------------------

function changeTypeBadge(type: string): string {
  switch (type.toUpperCase()) {
    case "A": return "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300";
    case "M": return "bg-blue-100  text-blue-700  dark:bg-blue-900/40  dark:text-blue-300";
    case "D": return "bg-red-100   text-red-700   dark:bg-red-900/40   dark:text-red-300";
    case "R": return "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300";
    case "C": return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
    default:  return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400";
  }
}

// ---------------------------------------------------------------------------
// CommitFiles — expanded numstat body for a single commit row
// ---------------------------------------------------------------------------

interface CommitFilesProps {
  boardKey: string;
  sha: string;
  shortSha: string;
  summary: string;
}

function CommitFiles({ boardKey, sha, shortSha, summary }: CommitFilesProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery<GitCommitDetail, ApiRequestError>({
    queryKey: ["git", "commit", boardKey, sha],
    queryFn: () => api.git.getCommit(boardKey, sha),
    staleTime: 60_000,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="py-2 px-4 space-y-1.5 animate-pulse" aria-busy="true" aria-label="Loading commit files">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-5 rounded bg-slate-100 dark:bg-slate-800" />
        ))}
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="px-4 py-2 flex items-center gap-2 text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/20 rounded" role="alert">
        <span>Dosyalar yüklenemedi.</span>
        <button
          type="button"
          className="underline hover:opacity-80 focus:outline-none focus:ring-1 focus:ring-red-400 rounded"
          onClick={() => void refetch()}
        >
          Tekrar dene
        </button>
      </div>
    );
  }

  return (
    <div>
      {/* File list */}
      <ul role="list" className="divide-y divide-slate-100 dark:divide-slate-800">
        {data.files.map((file) => {
          const isActive = selectedPath === file.path;
          const isBinary = file.is_binary;

          return (
            <li key={`${file.change_type}-${file.path}`}>
              {/* File row */}
              <div
                role={isBinary ? "presentation" : "button"}
                tabIndex={isBinary ? -1 : 0}
                aria-disabled={isBinary}
                aria-pressed={isActive}
                aria-label={isBinary ? undefined : `View diff for ${file.path}`}
                onClick={() => {
                  if (isBinary) return;
                  setSelectedPath(isActive ? null : file.path);
                }}
                onKeyDown={(e) => {
                  if (isBinary) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedPath(isActive ? null : file.path);
                  }
                }}
                className={cn(
                  "flex items-center gap-2 px-4 py-1.5 text-xs",
                  isBinary
                    ? "cursor-default"
                    : "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800 focus:outline-none focus:ring-1 focus:ring-inset focus:ring-indigo-400",
                  isActive && !isBinary && "bg-indigo-50 dark:bg-indigo-900/20"
                )}
              >
                {/* change_type badge */}
                <span
                  className={cn(
                    "rounded px-1 py-0.5 text-[10px] font-semibold font-mono flex-shrink-0",
                    changeTypeBadge(file.change_type)
                  )}
                  title={file.change_type}
                >
                  {file.change_type.toUpperCase()}
                </span>

                {/* Path (rename: old → new) */}
                <span className="font-mono text-[11px] flex-1 truncate text-slate-700 dark:text-slate-200">
                  {file.old_path && file.old_path !== file.path
                    ? `${file.old_path} → ${file.path}`
                    : file.path}
                </span>

                {/* Binary chip */}
                {isBinary && (
                  <span className="rounded bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 text-[10px] text-slate-500 dark:text-slate-400 flex-shrink-0">
                    binary
                  </span>
                )}

                {/* +/- counts */}
                {!isBinary && (
                  <>
                    <span className="text-green-600 dark:text-green-400 font-mono text-[10px] flex-shrink-0">
                      +{file.additions}
                    </span>
                    <span className="text-red-600 dark:text-red-400 font-mono text-[10px] flex-shrink-0">
                      -{file.deletions}
                    </span>
                  </>
                )}
              </div>

              {/* Inline DiffViewer when file selected */}
              {isActive && !isBinary && (
                <div className="border-t border-slate-100 dark:border-slate-800">
                  {/* Commit banner — AC4: "hangi commit?" */}
                  <div className="flex items-center gap-2 px-4 py-1.5 bg-slate-50 dark:bg-slate-800/60 text-[11px] text-slate-600 dark:text-slate-400">
                    <GitCommit className="h-3 w-3 shrink-0 text-slate-400" aria-hidden="true" />
                    <span className="font-mono text-slate-500">{shortSha}</span>
                    <span className="text-slate-400">·</span>
                    <span className="truncate">{summary}</span>
                  </div>
                  {/* DiffViewer */}
                  <div className="p-3">
                    <DiffViewer
                      fetch={{ kind: "commit", boardKey, sha, path: file.path }}
                    />
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {data.files.length === 0 && (
        <p className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400 italic">
          Bu commit'te dosya değişikliği yok.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TicketCommits (public export)
// ---------------------------------------------------------------------------

interface TicketCommitsProps {
  ticketKey: string;
  boardKey: string;
}

export function TicketCommits({ ticketKey, boardKey }: TicketCommitsProps) {
  const qc = useQueryClient();
  const [expandedSha, setExpandedSha] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["ticket-commits", ticketKey],
    queryFn: () => api.git.getTicketCommits(ticketKey),
    staleTime: 30_000,
    retry: false,
  });

  // --- loading ---
  if (isLoading) {
    return (
      <div className="space-y-1 animate-pulse" aria-busy="true" aria-label="Loading commits">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-8 rounded bg-slate-100 dark:bg-slate-800" />
        ))}
      </div>
    );
  }

  // --- error ---
  if (error) {
    // 409 RepoNotConfigured
    if (error instanceof ApiRequestError && error.status === 409) {
      return (
        <div className="rounded border border-slate-200 dark:border-slate-700 px-4 py-3 text-xs text-slate-600 dark:text-slate-400">
          Repo bağlı değil. Commit listesi kullanılamıyor.{" "}
          <a
            href={`/boards/${boardKey}/settings`}
            className="text-indigo-600 dark:text-indigo-400 underline hover:opacity-80"
          >
            Settings'ten bağlayabilirsiniz
          </a>
          .
        </div>
      );
    }
    return (
      <div
        className="rounded border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/20 px-4 py-3 text-xs text-red-700 dark:text-red-300 flex items-center gap-2"
        role="alert"
      >
        <span>Commit'ler yüklenemedi.</span>
        <button
          type="button"
          className="underline hover:opacity-80 focus:outline-none focus:ring-1 focus:ring-red-400 rounded"
          onClick={() => void refetch()}
        >
          Tekrar dene
        </button>
        <button
          type="button"
          className="underline hover:opacity-80 focus:outline-none focus:ring-1 focus:ring-red-400 rounded"
          onClick={() => void qc.invalidateQueries({ queryKey: ["ticket-commits", ticketKey] })}
        >
          Yenile
        </button>
      </div>
    );
  }

  // --- empty ---
  if (!data || data.commits.length === 0) {
    return (
      <p className="text-xs text-slate-500 dark:text-slate-400 italic">
        Henüz commit yok.
      </p>
    );
  }

  return (
    <div className="rounded border border-slate-200 dark:border-slate-700 overflow-hidden">
      <ul role="list" className="divide-y divide-slate-100 dark:divide-slate-800">
        {data.commits.map((commit) => {
          const isExpanded = expandedSha === commit.sha;

          return (
            <li key={commit.sha}>
              {/* Commit row */}
              <div
                role="button"
                tabIndex={0}
                aria-expanded={isExpanded}
                aria-label={`${isExpanded ? "Collapse" : "Expand"} commit ${commit.short_sha}: ${commit.summary}`}
                onClick={() => setExpandedSha(isExpanded ? null : commit.sha)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setExpandedSha(isExpanded ? null : commit.sha);
                  }
                }}
                className="flex items-start gap-2 px-3 py-2 cursor-pointer select-none hover:bg-slate-50 dark:hover:bg-slate-800/50 focus:outline-none focus:ring-1 focus:ring-inset focus:ring-indigo-400 transition-colors"
              >
                {/* Chevron */}
                <ChevronRight
                  className={cn(
                    "h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-slate-400 transition-transform",
                    isExpanded && "rotate-90"
                  )}
                  aria-hidden="true"
                />

                {/* short_sha */}
                <span className="font-mono text-[11px] text-indigo-600 dark:text-indigo-400 flex-shrink-0 pt-0.5">
                  {commit.short_sha}
                </span>

                {/* summary */}
                <span className="flex-1 text-xs text-slate-700 dark:text-slate-200 truncate pt-0.5">
                  {commit.summary}
                </span>

                {/* +/- */}
                <span className="text-green-600 dark:text-green-400 font-mono text-[10px] flex-shrink-0 pt-0.5">
                  +{commit.additions}
                </span>
                <span className="text-red-600 dark:text-red-400 font-mono text-[10px] flex-shrink-0 pt-0.5">
                  -{commit.deletions}
                </span>

                {/* file count */}
                <span className="text-slate-400 dark:text-slate-500 text-[10px] flex-shrink-0 pt-0.5">
                  {commit.files_changed} {commit.files_changed === 1 ? "file" : "files"}
                </span>

                {/* author + time */}
                <span
                  className="text-slate-400 dark:text-slate-500 text-[10px] flex-shrink-0 pt-0.5 hidden sm:block"
                  title={new Date(commit.committed_at).toLocaleString()}
                >
                  {commit.author_name} · {relativeTime(commit.committed_at)}
                </span>
              </div>

              {/* Expanded: file list + diff */}
              {isExpanded && (
                <div className="border-t border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900">
                  <CommitFiles
                    boardKey={boardKey}
                    sha={commit.sha}
                    shortSha={commit.short_sha}
                    summary={commit.summary}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
