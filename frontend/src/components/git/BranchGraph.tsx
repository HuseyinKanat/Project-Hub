/**
 * BranchGraph.tsx — PH-167 (SourceTree UX rework)
 *
 * SourceTree-style vertical commit list with SVG lane gutter.
 * Replaces the @xyflow/react node-graph from G10/PH-159.
 *
 * Layout (3-pane):
 *   [Branch list sidebar] | [Commit list + SVG lane gutter] | [Diff panel (when commit selected)]
 *
 * Features:
 *   - Commits sorted newest-first (server-side order preserved)
 *   - Compact row: lane gutter (colored line+dot+merge) | short-sha | summary | refs | author | time
 *   - Branch sidebar: click branch → filtered commit list; "All" shows full graph
 *   - Commit click → DiffViewer panel (getCommitDiff) on the right
 *   - WS live-sync: new commits appear at top via queryClient.invalidateQueries in BoardDetail
 *   - Dark/light theme, keyboard-accessible rows
 *   - No new dependencies (removed @xyflow/react from this component)
 */

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, AlertCircle, Loader2, RefreshCw, X } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { DiffViewer } from "@/components/diff";
import { cn } from "@/lib/utils";
import type { GitBranchEntry, GitCommitSummary } from "@/types/git";
import { assignLanes, laneColor } from "./branchGraphLayout";

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------
/** Pixel width per lane column in SVG gutter */
const LANE_PX = 14;
/** Row height for each commit row */
const ROW_H = 36;
/** Max lanes to render (cap to avoid runaway width on repos with many branches) */
const MAX_LANES = 10;
/** Padding on left side of gutter */
const GUTTER_PAD = 6;
/** Data fetch limits */
const GRAPH_LIMIT = 150;
const BRANCH_LIMIT = 80;

// ---------------------------------------------------------------------------
// Helpers
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
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

/** Lane x-center in the gutter SVG (capped at MAX_LANES) */
function laneCx(lane: number): number {
  const capped = Math.min(lane, MAX_LANES - 1);
  return GUTTER_PAD + capped * LANE_PX + LANE_PX / 2;
}

// ---------------------------------------------------------------------------
// Per-row SVG dot cell — lightweight, only renders the dot and pass-through lines
// ---------------------------------------------------------------------------

interface GutterCellProps {
  commit: GitCommitSummary;
  rowIdx: number;
  laneOfSha: Map<string, number>;
  shaIndex: Map<string, number>;
  activeLanesAtRow: Set<number>; // lanes that pass through (have commits above AND below)
  isSelected: boolean;
  isNew: boolean;
  totalRows: number;
}

function GutterCell({
  commit,
  laneOfSha,
  shaIndex,
  activeLanesAtRow,
  isSelected,
  isNew,
}: GutterCellProps) {
  const lane = Math.min(laneOfSha.get(commit.sha) ?? 0, MAX_LANES - 1);
  const color = laneColor(lane);
  const cx = laneCx(lane);
  const cy = ROW_H / 2;
  const isMerge = commit.parents.length > 1;
  const gutterW = MAX_LANES * LANE_PX + GUTTER_PAD * 2;

  // Build SVG elements for this row
  const elements: React.ReactNode[] = [];

  // 1. Pass-through lines for other active lanes
  activeLanesAtRow.forEach((activeLane) => {
    if (activeLane === lane) return; // skip own lane (drawn separately)
    const ax = laneCx(activeLane);
    elements.push(
      <line
        key={`pass-${activeLane}`}
        x1={ax} y1={0}
        x2={ax} y2={ROW_H}
        stroke={laneColor(activeLane)}
        strokeWidth={2}
        opacity={0.18}
      />
    );
  });

  // 2. Own lane line (above and below dot)
  elements.push(
    <line key="own-top" x1={cx} y1={0} x2={cx} y2={cy - (isMerge ? 5 : 4)} stroke={color} strokeWidth={2} opacity={0.55} />,
    <line key="own-bot" x1={cx} y1={cy + (isMerge ? 5 : 4)} x2={cx} y2={ROW_H} stroke={color} strokeWidth={2} opacity={0.55} />,
  );

  // 3. Merge curves to parent lanes
  for (let pi = 0; pi < commit.parents.length; pi++) {
    const parentSha = commit.parents[pi]!;
    const parentLane = Math.min(laneOfSha.get(parentSha) ?? lane, MAX_LANES - 1);
    if (pi === 0 && parentLane === lane) continue; // same lane, already drawn
    const parentIdx = shaIndex.get(parentSha);
    if (parentIdx === undefined) continue;

    const px = laneCx(parentLane);
    const mergeColor = laneColor(pi === 0 ? lane : parentLane);
    // Draw a short angled line at the bottom of this row going toward parent lane
    elements.push(
      <line
        key={`merge-${pi}`}
        x1={cx} y1={cy + (isMerge ? 5 : 4)}
        x2={px} y2={ROW_H}
        stroke={mergeColor}
        strokeWidth={2}
        opacity={0.6}
        strokeDasharray={pi > 0 ? "3 3" : undefined}
      />
    );
  }

  // 4. Dot
  elements.push(
    <circle
      key="dot"
      cx={cx}
      cy={cy}
      r={isMerge ? 5 : 3.5}
      fill={isSelected ? "#fff" : color}
      stroke={color}
      strokeWidth={isSelected ? 2 : 1.5}
      style={isNew ? { filter: `drop-shadow(0 0 3px ${color})` } : undefined}
    />
  );

  return (
    <svg
      aria-hidden="true"
      width={gutterW}
      height={ROW_H}
      style={{ display: "block", flexShrink: 0 }}
    >
      {elements}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// CommitRow — single compact row
// ---------------------------------------------------------------------------

interface CommitRowProps {
  commit: GitCommitSummary;
  rowIdx: number;
  laneOfSha: Map<string, number>;
  shaIndex: Map<string, number>;
  activeLanesAtRow: Set<number>;
  isSelected: boolean;
  isNew: boolean;
  boardKey: string;
  totalRows: number;
  onClick: () => void;
}

function CommitRow({
  commit,
  rowIdx,
  laneOfSha,
  shaIndex,
  activeLanesAtRow,
  isSelected,
  isNew,
  boardKey,
  totalRows,
  onClick,
}: CommitRowProps) {
  const lane = Math.min(laneOfSha.get(commit.sha) ?? 0, MAX_LANES - 1);
  const color = laneColor(lane);

  return (
    <div
      className={cn(
        "flex items-center border-b border-slate-100 dark:border-slate-800",
      )}
      style={{ height: ROW_H, minHeight: ROW_H }}
    >
      {/* SVG gutter cell */}
      <GutterCell
        commit={commit}
        rowIdx={rowIdx}
        laneOfSha={laneOfSha}
        shaIndex={shaIndex}
        activeLanesAtRow={activeLanesAtRow}
        isSelected={isSelected}
        isNew={isNew}
        totalRows={totalRows}
      />

      {/* Clickable row content */}
      <button
        type="button"
        role="listitem"
        aria-pressed={isSelected}
        onClick={onClick}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-2 px-2 py-0 text-left",
          "transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/40",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500",
          isSelected && "bg-indigo-50 dark:bg-indigo-900/20",
          isNew && "bg-amber-50 dark:bg-amber-900/10",
        )}
        style={{ height: ROW_H }}
      >
        {/* short sha */}
        <span
          className="w-14 flex-shrink-0 font-mono text-[11px]"
          style={{ color: isSelected ? color : undefined }}
          title={commit.sha}
        >
          {isSelected
            ? <span style={{ color }}>{commit.short_sha}</span>
            : <span className="text-slate-400 dark:text-slate-500">{commit.short_sha}</span>
          }
        </span>

        {/* summary */}
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-xs",
            isSelected
              ? "font-medium text-slate-900 dark:text-slate-100"
              : "text-slate-700 dark:text-slate-300",
          )}
          title={commit.summary}
        >
          {commit.summary}
        </span>

        {/* ref badges */}
        {commit.refs.length > 0 && (
          <div className="flex flex-shrink-0 items-center gap-1 overflow-hidden">
            {commit.refs.slice(0, 2).map((ref) => {
              const isHEAD = ref === "HEAD" || ref.startsWith("HEAD ->");
              return (
                <span
                  key={ref}
                  className={cn(
                    "max-w-[80px] truncate rounded px-1 py-0.5 font-mono text-[10px] font-medium leading-none",
                    isHEAD
                      ? "bg-slate-700 text-white dark:bg-slate-200 dark:text-slate-900"
                      : "",
                  )}
                  style={!isHEAD ? { backgroundColor: color + "22", color } : undefined}
                  title={ref}
                >
                  {ref}
                </span>
              );
            })}
            {commit.refs.length > 2 && (
              <span className="rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                +{commit.refs.length - 2}
              </span>
            )}
          </div>
        )}

        {/* ticket keys */}
        {commit.ticket_keys.length > 0 && (
          <div className="flex flex-shrink-0 items-center gap-1">
            {commit.ticket_keys.slice(0, 2).map((key) => (
              <Link
                key={key}
                to={`/boards/${boardKey}/tickets/${key}`}
                onClick={(e) => e.stopPropagation()}
                className="rounded bg-indigo-50 px-1 py-0.5 text-[10px] font-medium text-indigo-700 hover:bg-indigo-100 dark:bg-indigo-900/30 dark:text-indigo-300 dark:hover:bg-indigo-900/50"
              >
                {key}
              </Link>
            ))}
          </div>
        )}

        {/* author */}
        <span className="hidden w-20 flex-shrink-0 truncate text-[11px] text-slate-400 dark:text-slate-500 sm:block">
          {commit.author_name}
        </span>

        {/* time */}
        <span className="w-14 flex-shrink-0 text-right text-[11px] text-slate-400 dark:text-slate-500">
          {relativeTime(commit.committed_at)}
        </span>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BranchSidebar
// ---------------------------------------------------------------------------

interface BranchSidebarProps {
  branches: GitBranchEntry[];
  selectedBranch: string | null;
  onSelectBranch: (branch: string | null) => void;
}

function BranchSidebar({ branches, selectedBranch, onSelectBranch }: BranchSidebarProps) {
  const sorted = [...branches].sort((a, b) => {
    if (a.is_default && !b.is_default) return -1;
    if (!a.is_default && b.is_default) return 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <aside
      aria-label="Branch list"
      className="flex w-44 flex-shrink-0 flex-col gap-0.5 overflow-y-auto rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-800"
    >
      <h2 className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
        Branches
      </h2>

      {/* All branches */}
      <button
        type="button"
        aria-pressed={selectedBranch === null}
        onClick={() => onSelectBranch(null)}
        className={cn(
          "flex items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors",
          "hover:bg-slate-50 dark:hover:bg-slate-700/50",
          selectedBranch === null
            ? "bg-indigo-50 font-semibold text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300"
            : "text-slate-700 dark:text-slate-300",
        )}
      >
        <span className="h-2 w-2 flex-shrink-0 rounded-full bg-slate-300 dark:bg-slate-500" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">All</span>
      </button>

      {sorted.map((branch, idx) => {
        const color = laneColor(idx);
        const isSelected = selectedBranch === branch.name;
        return (
          <button
            key={branch.name}
            type="button"
            aria-pressed={isSelected}
            onClick={() => onSelectBranch(branch.name)}
            className={cn(
              "flex items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors",
              "hover:bg-slate-50 dark:hover:bg-slate-700/50",
              isSelected
                ? "bg-indigo-50 font-semibold dark:bg-indigo-900/30"
                : "text-slate-700 dark:text-slate-300",
            )}
          >
            <span
              className="h-2 w-2 flex-shrink-0 rounded-full"
              style={{ backgroundColor: color }}
              aria-hidden="true"
            />
            <span
              className="min-w-0 flex-1 truncate"
              title={branch.name}
              style={isSelected ? { color } : undefined}
            >
              {branch.name}
            </span>
            {branch.is_default && (
              <span className="flex-shrink-0 rounded bg-slate-900 px-1 py-0.5 font-mono text-[9px] text-white dark:bg-slate-600">
                HEAD
              </span>
            )}
          </button>
        );
      })}

      {branches.length === 0 && (
        <p className="px-1 text-[11px] text-slate-400 dark:text-slate-500">No branches</p>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// CommitDiffPanel — right panel for selected commit diff
// ---------------------------------------------------------------------------

interface CommitDiffPanelProps {
  boardKey: string;
  sha: string;
  summary: string;
  onClose: () => void;
}

function CommitDiffPanel({ boardKey, sha, summary, onClose }: CommitDiffPanelProps) {
  return (
    <aside
      role="complementary"
      aria-label="Commit diff panel"
      className={cn(
        "flex w-full flex-shrink-0 flex-col gap-2 lg:w-96",
        "rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-800",
        "overflow-y-auto",
      )}
      style={{ maxHeight: "calc(100vh - 220px)" }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[11px] text-slate-400 dark:text-slate-500">{sha.slice(0, 12)}</p>
          <p
            className="mt-0.5 truncate text-xs font-medium text-slate-800 dark:text-slate-200"
            title={summary}
          >
            {summary}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close diff panel"
          onClick={onClose}
          className="flex-shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <DiffViewer fetch={{ kind: "commit", boardKey, sha }} />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// BranchGraph (public export)
// ---------------------------------------------------------------------------

interface BranchGraphProps {
  boardKey: string;
  /** Shas that just arrived via WS git_synced — highlight for 3s. */
  highlightedShas?: Set<string>;
  onCommitSelect?: (sha: string) => void;
  onBranchSelect?: (branch: string) => void;
}

export function BranchGraph({
  boardKey,
  highlightedShas = new Set<string>(),
  onCommitSelect,
  onBranchSelect,
}: BranchGraphProps) {
  const [selectedBranch, setSelectedBranch] = useState<string | null>(null);
  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // Data
  // ---------------------------------------------------------------------------
  const graphQuery = useQuery({
    queryKey: ["git", boardKey, "graph", GRAPH_LIMIT],
    queryFn: () => api.git.getGraph(boardKey, { limit: GRAPH_LIMIT }),
    enabled: Boolean(boardKey),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const statusQuery = useQuery({
    queryKey: ["git", boardKey, "status"],
    queryFn: () => api.git.getStatus(boardKey),
    enabled: Boolean(boardKey),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const branchCommitsQuery = useQuery({
    queryKey: ["git", boardKey, "commits", selectedBranch, BRANCH_LIMIT],
    queryFn: () =>
      api.git.listCommits(boardKey, {
        branch: selectedBranch ?? undefined,
        limit: BRANCH_LIMIT,
      }),
    enabled: Boolean(boardKey && selectedBranch),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  // ---------------------------------------------------------------------------
  // Derived: lane map (only for full graph, reused for branch filter display)
  // ---------------------------------------------------------------------------
  const allCommits = graphQuery.data?.commits ?? [];
  const branches = graphQuery.data?.branches ?? [];

  const memoKey = useMemo(() => {
    if (allCommits.length === 0) return "";
    return `${allCommits[0]!.sha}_${allCommits[allCommits.length - 1]!.sha}_${allCommits.length}`;
  }, [allCommits]);

  const laneOfSha = useMemo(
    () => assignLanes(allCommits, branches),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [memoKey],
  );

  // displayCommits: branch-filtered or all
  const displayCommits: GitCommitSummary[] = selectedBranch
    ? (branchCommitsQuery.data?.commits ?? [])
    : allCommits;

  // shaIndex: sha → row index in displayCommits (for merge-line lookup)
  const shaIndex = useMemo(() => {
    const m = new Map<string, number>();
    displayCommits.forEach((c, i) => m.set(c.sha, i));
    return m;
  }, [displayCommits]);

  // Per-row active lanes: for row i, which lanes have commits both above (row < i) and below (row > i)?
  // Computed by tracking lane "first" and "last" row index in displayCommits
  const perRowActiveLanes = useMemo((): Set<number>[] => {
    // Build lane extents over displayCommits
    const laneFirst = new Map<number, number>(); // lane → first (topmost) row
    const laneLast = new Map<number, number>();  // lane → last (bottommost) row

    displayCommits.forEach((c, rowIdx) => {
      const lane = Math.min(laneOfSha.get(c.sha) ?? 0, MAX_LANES - 1);
      if (!laneFirst.has(lane)) laneFirst.set(lane, rowIdx);
      laneLast.set(lane, rowIdx);
    });

    // For each row, build the set of lanes that span across it (first < row < last)
    return displayCommits.map((_c, rowIdx) => {
      const active = new Set<number>();
      laneFirst.forEach((first, lane) => {
        const last = laneLast.get(lane)!;
        if (first < rowIdx && rowIdx < last) {
          active.add(lane);
        }
      });
      return active;
    });
  }, [displayCommits, laneOfSha]);

  // Selected commit object
  const selectedCommit = useMemo(
    () => displayCommits.find((c) => c.sha === selectedCommitSha) ?? null,
    [displayCommits, selectedCommitSha],
  );

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------
  const handleBranchSelect = useCallback(
    (branch: string | null) => {
      setSelectedBranch(branch);
      setSelectedCommitSha(null);
      if (branch) onBranchSelect?.(branch);
    },
    [onBranchSelect],
  );

  const handleCommitClick = useCallback(
    (sha: string) => {
      setSelectedCommitSha((prev) => (prev === sha ? null : sha));
      onCommitSelect?.(sha);
    },
    [onCommitSelect],
  );

  // ---------------------------------------------------------------------------
  // Early-exit states
  // ---------------------------------------------------------------------------
  if (graphQuery.isLoading || statusQuery.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center gap-2">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
        <span className="text-sm text-slate-500">Yükleniyor…</span>
      </div>
    );
  }

  if (statusQuery.data && !statusQuery.data.connected) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <GitBranch className="h-10 w-10 text-slate-300 dark:text-slate-600" />
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Bu board&apos;a repo bağlı değil.{" "}
          <Link to={`/boards/${boardKey}/settings`} className="text-indigo-600 hover:underline dark:text-indigo-400">
            Settings&apos;ten bağlayın
          </Link>
        </p>
      </div>
    );
  }

  if (graphQuery.error) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3">
        <AlertCircle className="h-8 w-8 text-red-400" />
        <p className="text-sm text-slate-500">Graph yüklenirken hata oluştu.</p>
        <button
          type="button"
          onClick={() => void graphQuery.refetch()}
          className="flex items-center gap-1 rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Tekrar dene
        </button>
      </div>
    );
  }

  if (allCommits.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <GitBranch className="h-10 w-10 text-slate-300 dark:text-slate-600" />
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Henüz commit yok.
          <br />
          <span className="text-xs text-slate-400">Repo&apos;ya commit push edilince burası otomatik güncellenir.</span>
        </p>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Main 3-pane layout
  // ---------------------------------------------------------------------------
  const gutterW = MAX_LANES * LANE_PX + GUTTER_PAD * 2;

  return (
    <div
      className="flex gap-3"
      style={{ height: "calc(100vh - 220px)", minHeight: 480 }}
    >
      {/* Pane 1: Branch sidebar */}
      <BranchSidebar
        branches={branches}
        selectedBranch={selectedBranch}
        onSelectBranch={handleBranchSelect}
      />

      {/* Pane 2: Commit list */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
        {/* Column header */}
        <div
          className="flex flex-shrink-0 items-center gap-2 border-b border-slate-100 bg-slate-50 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-500"
          aria-hidden="true"
        >
          <span style={{ width: gutterW, flexShrink: 0 }} />
          <span className="w-14 flex-shrink-0">SHA</span>
          <span className="min-w-0 flex-1">Message</span>
          <span className="hidden w-20 flex-shrink-0 sm:block">Author</span>
          <span className="w-14 flex-shrink-0 text-right">Time</span>
        </div>

        {/* Scrollable commit list */}
        <div role="list" aria-label="Commit history" className="flex-1 overflow-y-auto">
          {/* Loading branch commits */}
          {selectedBranch && branchCommitsQuery.isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
            </div>
          )}

          {displayCommits.map((commit, rowIdx) => (
            <CommitRow
              key={commit.sha}
              commit={commit}
              rowIdx={rowIdx}
              laneOfSha={laneOfSha}
              shaIndex={shaIndex}
              activeLanesAtRow={perRowActiveLanes[rowIdx] ?? new Set()}
              isSelected={commit.sha === selectedCommitSha}
              isNew={highlightedShas.has(commit.sha)}
              boardKey={boardKey}
              totalRows={displayCommits.length}
              onClick={() => handleCommitClick(commit.sha)}
            />
          ))}

          {selectedBranch && !branchCommitsQuery.isLoading && displayCommits.length === 0 && (
            <div className="flex h-32 items-center justify-center text-sm text-slate-400 dark:text-slate-500">
              Bu branch&apos;te commit yok.
            </div>
          )}
        </div>
      </div>

      {/* Pane 3: Commit diff panel (conditional) */}
      {selectedCommit && (
        <CommitDiffPanel
          boardKey={boardKey}
          sha={selectedCommit.sha}
          summary={selectedCommit.summary}
          onClose={() => setSelectedCommitSha(null)}
        />
      )}
    </div>
  );
}
