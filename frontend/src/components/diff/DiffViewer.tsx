/**
 * DiffViewer.tsx — container component, 2-mode API.
 * G9 / PH-158
 *
 * Mode 1 — data-passed:
 *   <DiffViewer files={FileDiff[]} />
 *
 * Mode 2 — fetch-self (TanStack Query):
 *   <DiffViewer fetch={{ kind: 'commit', boardKey: 'PH', sha: '...' }} />
 *   <DiffViewer fetch={{ kind: 'range', boardKey: 'PH', base: '...', head: '...' }} />
 *
 * Features:
 *   - Header: file count + total +N / -M summary + global truncated warning
 *   - Per-file expand/collapse toggle (default expanded)
 *   - Loading / error states for fetch mode
 *   - Empty state for files=[]
 */
import { useQuery } from "@tanstack/react-query";

import { api, ApiRequestError } from "@/api/client";
import type { FileDiff, DiffResponse, RangeDiffResponse } from "@/types/git";
import { FileDiffView } from "./FileDiffView";

// ---------------------------------------------------------------------------
// Prop types
// ---------------------------------------------------------------------------

type FetchCommit = {
  kind: "commit";
  boardKey: string;
  sha: string;
  /** Optional path filter: only show diff for this specific file. */
  path?: string;
};

type FetchRange = {
  kind: "range";
  boardKey: string;
  base: string;
  head: string;
};

type FetchSpec = FetchCommit | FetchRange;

interface DiffViewerDataProps {
  /** Pre-loaded files array (data-passed mode). */
  files: FileDiff[];
  fetch?: never;
  collapseThreshold?: number;
  truncated?: boolean;
  /**
   * Render the internal "N files changed +N -M" summary header.
   * Default true; pass false when an outer chrome already shows the stat
   * (PH-188 — the Branch Graph diff panel's `.diff-stat` owns it, so the
   * embedded DiffViewer suppresses its own to avoid a duplicate header).
   */
  showSummary?: boolean;
}

interface DiffViewerFetchProps {
  files?: never;
  /** Fetch spec (fetch-self mode). */
  fetch: FetchSpec;
  collapseThreshold?: number;
  /** Ignored in fetch mode — derived from response. */
  truncated?: never;
  /** See {@link DiffViewerDataProps.showSummary}. */
  showSummary?: boolean;
}

type DiffViewerProps = DiffViewerDataProps | DiffViewerFetchProps;

// ---------------------------------------------------------------------------
// Inner render (shared between modes)
// ---------------------------------------------------------------------------

function DiffViewerInner({
  files,
  collapseThreshold = 50,
  truncated = false,
  showSummary = true,
}: {
  files: FileDiff[];
  collapseThreshold?: number;
  truncated?: boolean;
  showSummary?: boolean;
}) {
  const totalAdd = files.reduce((s, f) => s + f.additions, 0);
  const totalDel = files.reduce((s, f) => s + f.deletions, 0);

  return (
    <div className="flex flex-col gap-2">
      {/* Summary header (suppressible — PH-188 branch-graph diff panel) */}
      {showSummary && (
        <div className="flex items-center gap-3 text-sm text-text-secondary flex-wrap">
          <span>
            <span className="font-semibold text-text-primary">
              {files.length}
            </span>{" "}
            {files.length === 1 ? "file" : "files"} changed
          </span>
          {(totalAdd > 0 || totalDel > 0) && (
            <>
              <span className="text-success font-mono text-xs">
                +{totalAdd}
              </span>
              <span className="text-danger font-mono text-xs">
                -{totalDel}
              </span>
            </>
          )}
          {truncated && (
            <span className="rounded bg-warning-soft px-2 py-0.5 text-xs font-medium text-warning">
              Some diffs truncated (response cap hit)
            </span>
          )}
        </div>
      )}

      {/* Empty state */}
      {files.length === 0 ? (
        <div className="rounded-md border border-hairline px-4 py-8 text-center text-sm text-text-muted">
          No file changes
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {files.map((file) => (
            <FileDiffView
              key={`${file.change_type}-${file.path}`}
              file={file}
              collapseThreshold={collapseThreshold}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Commit diff hook
// ---------------------------------------------------------------------------

function useCommitDiff(boardKey: string, sha: string, path?: string) {
  return useQuery<DiffResponse, ApiRequestError>({
    queryKey: ["git", "diff", "commit", boardKey, sha, path ?? null],
    queryFn: () => api.git.getCommitDiff(boardKey, sha, path ? { path } : undefined),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Range diff hook
// ---------------------------------------------------------------------------

function useRangeDiff(boardKey: string, base: string, head: string) {
  return useQuery<RangeDiffResponse, ApiRequestError>({
    queryKey: ["git", "diff", "range", boardKey, base, head],
    queryFn: () => api.git.getRangeDiff(boardKey, { base, head }),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Error display helper
// ---------------------------------------------------------------------------

function DiffError({ error }: { error: ApiRequestError | Error | null }) {
  const msg = error instanceof ApiRequestError
    ? `${error.status} — ${error.message}`
    : (error?.message ?? "Unknown error");
  return (
    <div className="rounded-md border border-danger/40 bg-danger-soft px-4 py-3 text-sm text-danger" role="alert">
      Failed to load diff: {msg}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fetch-mode sub-components
// ---------------------------------------------------------------------------

function CommitDiffViewer({
  boardKey,
  sha,
  path,
  collapseThreshold,
  showSummary,
}: {
  boardKey: string;
  sha: string;
  path?: string;
  collapseThreshold?: number;
  showSummary?: boolean;
}) {
  const { data, isLoading, error } = useCommitDiff(boardKey, sha, path);

  if (isLoading) {
    return (
      <div className="py-6 text-center text-sm text-text-muted" role="status">
        Loading diff…
      </div>
    );
  }
  if (error || !data) {
    return <DiffError error={error} />;
  }

  return (
    <DiffViewerInner
      files={data.files}
      collapseThreshold={collapseThreshold}
      truncated={data.truncated}
      showSummary={showSummary}
    />
  );
}

function RangeDiffViewer({
  boardKey,
  base,
  head,
  collapseThreshold,
  showSummary,
}: {
  boardKey: string;
  base: string;
  head: string;
  collapseThreshold?: number;
  showSummary?: boolean;
}) {
  const { data, isLoading, error } = useRangeDiff(boardKey, base, head);

  if (isLoading) {
    return (
      <div className="py-6 text-center text-sm text-text-muted" role="status">
        Loading diff…
      </div>
    );
  }
  if (error || !data) {
    return <DiffError error={error} />;
  }

  return (
    <DiffViewerInner
      files={data.files}
      collapseThreshold={collapseThreshold}
      truncated={data.truncated}
      showSummary={showSummary}
    />
  );
}

// ---------------------------------------------------------------------------
// DiffViewer (public export)
// ---------------------------------------------------------------------------

export function DiffViewer(props: DiffViewerProps) {
  if (props.fetch) {
    const spec = props.fetch;
    if (spec.kind === "commit") {
      return (
        <CommitDiffViewer
          boardKey={spec.boardKey}
          sha={spec.sha}
          path={spec.path}
          collapseThreshold={props.collapseThreshold}
          showSummary={props.showSummary}
        />
      );
    }
    return (
      <RangeDiffViewer
        boardKey={spec.boardKey}
        base={spec.base}
        head={spec.head}
        collapseThreshold={props.collapseThreshold}
        showSummary={props.showSummary}
      />
    );
  }

  return (
    <DiffViewerInner
      files={props.files}
      collapseThreshold={props.collapseThreshold}
      truncated={props.truncated}
      showSummary={props.showSummary}
    />
  );
}
