/**
 * BranchGraph.tsx — PH-167 (SourceTree UX rework) · PH-175 (Cyan-on-Black) ·
 *                    PH-179 (continuous bezier lanes + floating glass detail card)
 *
 * SourceTree/GitKraken-style vertical commit list with a CONTINUOUS lane gutter.
 *
 * Layout (3-pane):
 *   [Branch sidebar ~176px] | [Commit list (relative): lane-svg overlay + rows + floating card] | [Diff pane ~384px]
 *
 * PH-179 render topology change:
 *   - The per-row `<svg>` GutterCell (0->ROW_H segments — visible seams) is GONE.
 *   - ONE absolutely-positioned, full-height `<svg>` overlay sits behind the rows,
 *     rendering continuous cubic-bezier lane paths + commit dots via
 *     `computeLanePaths` (branchGraphLayout.ts). Rows get `padding-left: gutterW`
 *     so their text clears the gutter; the overlay scrolls with the rows (it lives
 *     inside the same scroll container, height = content height).
 *   - Commit click reveals a floating GLASS detail card (quick-look: mono SHA,
 *     summary, N files, +adds/−dels, ticket chip) anchored near the row. A
 *     "View diff" affordance opens/keeps the existing right-hand DiffViewer pane
 *     (deep-dive). Card dismissible via X / Esc / click-away / re-click.
 *
 * Invariants preserved: assignLanes/laneColor (frozen), git data fetching
 * (getGraph/listCommits/getStatus), WS live highlight (highlightedShas),
 * branch filtering, keyboard a11y, theme-aware lane colors (var(--lane-*)).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, AlertCircle, Loader2, RefreshCw, X } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { DiffViewer } from "@/components/diff";
import { cn } from "@/lib/utils";
import type {
  GitBranchEntry,
  GitCommitDetail,
  GitCommitSummary,
} from "@/types/git";
import {
  ROW_H,
  assignLanes,
  laneColor,
  computeLanePaths,
} from "./branchGraphLayout";

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------
/** Pixel width per lane column in the gutter SVG. */
const LANE_PX = 14;
/** Max lanes to render (cap width on repos with many branches). */
const MAX_LANES = 10;
/** Padding on left side of the gutter. */
const GUTTER_PAD = 8;
/** Data fetch limits */
const GRAPH_LIMIT = 150;
const BRANCH_LIMIT = 80;

/** Total gutter width (also the rows' padding-left). */
const GUTTER_W = MAX_LANES * LANE_PX + GUTTER_PAD * 2;

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

// ---------------------------------------------------------------------------
// LaneOverlay — ONE continuous full-height SVG behind the rows (PH-179)
//
// Renders the bezier lane geometry from computeLanePaths plus the commit dots.
// Absolutely positioned, pointer-events:none, aria-hidden; height = content
// height so it scrolls with the rows inside the shared scroll container.
// ---------------------------------------------------------------------------

interface LaneOverlayProps {
  displayCommits: GitCommitSummary[];
  laneOfSha: Map<string, number>;
  selectedSha: string | null;
  highlightedShas: Set<string>;
}

function LaneOverlay({
  displayCommits,
  laneOfSha,
  selectedSha,
  highlightedShas,
}: LaneOverlayProps) {
  const geom = useMemo(
    () =>
      computeLanePaths(
        displayCommits,
        laneOfSha,
        ROW_H,
        LANE_PX,
        GUTTER_PAD,
        MAX_LANES,
      ),
    [displayCommits, laneOfSha],
  );

  return (
    <svg
      aria-hidden="true"
      width={geom.gutterW}
      height={geom.height}
      className="pointer-events-none absolute left-0 top-0"
      style={{ overflow: "visible" }}
    >
      {/* Continuous lane runs + branch/merge bezier curves */}
      {geom.segments.map((seg, i) => (
        <path
          key={`seg-${i}`}
          d={seg.d}
          fill="none"
          stroke={seg.color}
          strokeWidth={2}
          strokeLinecap="round"
          opacity={seg.opacity}
        />
      ))}

      {/* Commit dots — drawn in the SAME svg so they sit exactly on the path */}
      {geom.dots.map((dot) => {
        const isSelected = dot.sha === selectedSha;
        const isNew = highlightedShas.has(dot.sha);
        return (
          <circle
            key={`dot-${dot.sha}`}
            cx={dot.cx}
            cy={dot.cy}
            r={dot.r}
            // Selected = hollow (fill bg-base + lane-color ring). New-commit =
            // soft cyan glow via drop-shadow (PH-175, motion-safe globally).
            fill={isSelected ? "var(--bg-base)" : dot.color}
            stroke={dot.color}
            strokeWidth={isSelected ? 2.5 : 1.5}
            style={
              isNew
                ? { filter: "drop-shadow(0 0 4px var(--accent))" }
                : undefined
            }
          />
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// FloatingDetailCard — glass quick-look on commit click (PH-179)
//
// Anchored near the selected row over the commit-list pane (which is relative).
// Shows mono 12-char SHA + summary + ticket chip immediately (from the
// GitCommitSummary already in hand); fetches getCommit for the stat row
// (N files / +adds / −dels), shows a skeleton while loading, hides the stat
// row on error. "View diff" opens/keeps the right-hand DiffViewer pane.
// ---------------------------------------------------------------------------

interface FloatingDetailCardProps {
  boardKey: string;
  commit: GitCommitSummary;
  rowIdx: number;
  totalRows: number;
  onViewDiff: () => void;
  onClose: () => void;
}

function FloatingDetailCard({
  boardKey,
  commit,
  rowIdx,
  totalRows,
  onViewDiff,
  onClose,
}: FloatingDetailCardProps) {
  // Cache-shared with TicketCommits' CommitFiles (same queryKey/endpoint).
  const { data, isLoading, isError } = useQuery<GitCommitDetail>({
    queryKey: ["git", "commit", boardKey, commit.sha],
    queryFn: () => api.git.getCommit(boardKey, commit.sha),
    staleTime: 60_000,
    retry: false,
  });

  const stats = useMemo(() => {
    if (!data) return null;
    let adds = 0;
    let dels = 0;
    for (const f of data.files) {
      adds += f.additions;
      dels += f.deletions;
    }
    return { files: data.files.length, adds, dels };
  }, [data]);

  // Anchor: default below the row; flip above when near the bottom so the
  // card never clips off the pane (R5).
  const flipAbove = totalRows > 4 && rowIdx > totalRows - 4;
  const anchorTop = flipAbove
    ? Math.max(4, rowIdx * ROW_H - 96)
    : rowIdx * ROW_H + ROW_H + 4;

  return (
    <div
      role="dialog"
      aria-label={`Commit ${commit.short_sha} details`}
      // Glass styling ported from branch-graph-row.html `.detail` -> F1 tokens.
      // boxShadow is inline (NOT a Tailwind arbitrary class): the comma inside
      // `shadow-[var(--shadow-lg),var(--glow-cyan-sm)]` breaks JIT parsing, so
      // the class silently never generates. Inline guarantees shadow + glow.
      className="absolute z-50 w-[252px] rounded-lg border p-[13px]"
      style={{
        right: 14,
        top: anchorTop,
        backgroundColor: "color-mix(in srgb, var(--bg-raised) 96%, transparent)",
        borderColor: "var(--hairline-cyan)",
        boxShadow: "var(--shadow-lg), var(--glow-cyan-sm)",
      }}
      // Stop click-away handler (on the pane) from closing when interacting.
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[11px] text-text-muted">
          {commit.sha.slice(0, 12)}
        </span>
        <button
          type="button"
          aria-label="Close detail card"
          onClick={onClose}
          className="-mr-1 -mt-1 flex-shrink-0 rounded p-0.5 text-text-muted hover:bg-raised hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <p
        className="my-[5px] text-[12.5px] font-medium leading-snug text-text-primary line-clamp-2"
        title={commit.summary}
      >
        {commit.summary}
      </p>

      {/* Stat row: skeleton while loading, hidden on error, values when ready */}
      {!isError && (
        <div className="mb-2.5 flex items-center gap-2.5 font-mono text-[11px]">
          {isLoading || !stats ? (
            <span
              className="h-3 w-32 animate-pulse rounded bg-raised"
              aria-label="Loading commit stats"
            />
          ) : (
            <>
              <span className="text-text-muted">{stats.files} files</span>
              <span className="text-success">+{stats.adds}</span>
              <span className="text-danger">−{stats.dels}</span>
            </>
          )}
          {commit.ticket_keys.length > 0 && (
            <span className="ml-auto flex items-center gap-1">
              {commit.ticket_keys.slice(0, 2).map((key) => (
                <Link
                  key={key}
                  to={`/boards/${boardKey}/tickets/${key}`}
                  onClick={(e) => e.stopPropagation()}
                  className="rounded border border-hairline-cyan bg-accent-soft px-1.5 py-px font-mono text-[10px] text-accent hover:bg-accent-subtle focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {key}
                </Link>
              ))}
            </span>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={onViewDiff}
        className="w-full rounded-md border border-hairline-cyan bg-accent-soft px-2 py-1 text-[11px] font-medium text-accent transition-colors hover:bg-accent-subtle focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        View diff
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CommitRow — single compact row (NO per-row svg; gutter handled by overlay)
// ---------------------------------------------------------------------------

interface CommitRowProps {
  commit: GitCommitSummary;
  laneOfSha: Map<string, number>;
  isSelected: boolean;
  isNew: boolean;
  boardKey: string;
  onClick: () => void;
}

function CommitRow({
  commit,
  laneOfSha,
  isSelected,
  isNew,
  boardKey,
  onClick,
}: CommitRowProps) {
  const lane = Math.min(laneOfSha.get(commit.sha) ?? 0, MAX_LANES - 1);
  const color = laneColor(lane);

  return (
    <button
      type="button"
      role="listitem"
      aria-pressed={isSelected}
      // Stop the pane's click-away (mousedown) from firing; the button's own
      // onClick handles selection/toggle. Keeps re-click-to-dismiss working.
      onMouseDown={(e) => e.stopPropagation()}
      onClick={onClick}
      className={cn(
        "relative flex w-full items-center gap-3 border-b border-hairline pr-4 text-left",
        "transition-colors hover:bg-raised",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
        isSelected && "bg-accent-subtle",
        isNew && "animate-glowin",
      )}
      style={{ height: ROW_H, paddingLeft: GUTTER_W }}
    >
      {/* short sha — cyan/lane color when selected, muted otherwise */}
      <span
        className="w-[70px] flex-shrink-0 font-mono text-xs"
        title={commit.sha}
        style={{ color: isSelected ? color : undefined }}
      >
        {isSelected ? (
          commit.short_sha
        ) : (
          <span className="text-text-muted">{commit.short_sha}</span>
        )}
      </span>

      {/* summary + ref badges */}
      <span
        className={cn(
          "flex min-w-0 flex-1 items-center gap-2 truncate text-[13px]",
          isSelected ? "font-medium text-text-primary" : "text-text-secondary",
        )}
        title={commit.summary}
      >
        <span className="truncate">{commit.summary}</span>

        {commit.refs.length > 0 && (
          <span className="flex flex-shrink-0 items-center gap-1 overflow-hidden">
            {commit.refs.slice(0, 2).map((ref) => {
              const isHEAD = ref === "HEAD" || ref.startsWith("HEAD ->");
              return (
                <span
                  key={ref}
                  className={cn(
                    "max-w-[80px] truncate rounded px-1.5 py-0.5 font-mono text-[10px] font-medium leading-none",
                    isHEAD
                      ? "border border-hairline-cyan bg-accent-soft text-accent"
                      : "border border-hairline bg-raised",
                  )}
                  style={
                    !isHEAD
                      ? {
                          backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)`,
                          color,
                        }
                      : undefined
                  }
                  title={ref}
                >
                  {ref}
                </span>
              );
            })}
            {commit.refs.length > 2 && (
              <span className="rounded bg-raised px-1 py-0.5 text-[10px] text-text-muted">
                +{commit.refs.length - 2}
              </span>
            )}
          </span>
        )}
      </span>

      {/* ticket keys */}
      {commit.ticket_keys.length > 0 && (
        <span className="flex flex-shrink-0 items-center gap-1">
          {commit.ticket_keys.slice(0, 2).map((key) => (
            <Link
              key={key}
              to={`/boards/${boardKey}/tickets/${key}`}
              onClick={(e) => e.stopPropagation()}
              className="rounded bg-accent-soft px-1.5 py-0.5 font-mono text-[10px] font-medium text-accent hover:bg-accent-subtle focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
            >
              {key}
            </Link>
          ))}
        </span>
      )}

      {/* author */}
      <span className="hidden w-24 flex-shrink-0 truncate text-[11px] text-text-muted sm:block">
        {commit.author_name}
      </span>

      {/* time */}
      <span className="w-14 flex-shrink-0 text-right text-[11px] text-text-muted">
        {relativeTime(commit.committed_at)}
      </span>
    </button>
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
      className="flex w-44 flex-shrink-0 flex-col gap-0.5 overflow-y-auto rounded-lg border border-hairline bg-surface p-2"
    >
      <h2 className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        Branches
      </h2>

      {/* All branches */}
      <button
        type="button"
        aria-pressed={selectedBranch === null}
        onClick={() => onSelectBranch(null)}
        className={cn(
          "flex items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors",
          "hover:bg-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          selectedBranch === null
            ? "bg-accent-soft font-semibold text-accent"
            : "text-text-secondary",
        )}
      >
        <span className="h-2 w-2 flex-shrink-0 rounded-full bg-text-muted" aria-hidden="true" />
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
              "hover:bg-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
              isSelected
                ? "bg-accent-soft font-semibold text-text-primary"
                : "text-text-secondary",
            )}
          >
            <span
              className="h-2 w-2 flex-shrink-0 rounded-full"
              style={{ backgroundColor: color }}
              aria-hidden="true"
            />
            <span
              className="min-w-0 flex-1 truncate font-mono"
              title={branch.name}
              style={isSelected ? { color } : undefined}
            >
              {branch.name}
            </span>
            {branch.is_default && (
              <span className="flex-shrink-0 rounded bg-accent-soft px-1 py-0.5 font-mono text-[9px] text-accent border border-hairline-cyan">
                HEAD
              </span>
            )}
          </button>
        );
      })}

      {branches.length === 0 && (
        <p className="px-1 text-[11px] text-text-muted">No branches</p>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// CommitDiffPanel — right pane (deep-dive); reuses DiffViewer unchanged.
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
        "rounded-lg border border-hairline bg-surface p-3",
        "overflow-y-auto",
      )}
      style={{ maxHeight: "calc(100vh - 220px)" }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[11px] text-text-muted">{sha.slice(0, 12)}</p>
          <p
            className="mt-0.5 truncate text-xs font-medium text-text-primary"
            title={summary}
          >
            {summary}
          </p>
        </div>
        <button
          type="button"
          aria-label="Close diff panel"
          onClick={onClose}
          className="flex-shrink-0 rounded p-0.5 text-text-muted hover:bg-raised hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
  // Card = quick-look (default on select). Diff pane = deep-dive (on "View diff").
  const [diffOpen, setDiffOpen] = useState(false);

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
  // Derived: lane map (full graph; reused for branch-filtered display)
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

  const displayCommits: GitCommitSummary[] = selectedBranch
    ? (branchCommitsQuery.data?.commits ?? [])
    : allCommits;

  const selectedRowIdx = useMemo(
    () => displayCommits.findIndex((c) => c.sha === selectedCommitSha),
    [displayCommits, selectedCommitSha],
  );

  const selectedCommit = selectedRowIdx >= 0 ? displayCommits[selectedRowIdx]! : null;

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------
  const closeSelection = useCallback(() => {
    setSelectedCommitSha(null);
    setDiffOpen(false);
  }, []);

  const handleBranchSelect = useCallback(
    (branch: string | null) => {
      setSelectedBranch(branch);
      closeSelection();
      if (branch) onBranchSelect?.(branch);
    },
    [onBranchSelect, closeSelection],
  );

  const handleCommitClick = useCallback(
    (sha: string) => {
      setSelectedCommitSha((prev) => {
        if (prev === sha) {
          setDiffOpen(false);
          return null; // re-click dismisses
        }
        return sha;
      });
      onCommitSelect?.(sha);
    },
    [onCommitSelect],
  );

  // Esc dismisses the card/selection.
  useEffect(() => {
    if (!selectedCommitSha) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSelection();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedCommitSha, closeSelection]);

  // ---------------------------------------------------------------------------
  // Early-exit states
  // ---------------------------------------------------------------------------
  if (graphQuery.isLoading || statusQuery.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center gap-2">
        <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
        <span className="text-sm text-text-secondary">Yükleniyor…</span>
      </div>
    );
  }

  if (statusQuery.data && !statusQuery.data.connected) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <GitBranch className="h-10 w-10 text-text-muted" />
        <p className="text-sm text-text-secondary">
          Bu board&apos;a repo bağlı değil.{" "}
          <Link to={`/boards/${boardKey}/settings`} className="text-accent hover:text-accent-hover hover:underline">
            Settings&apos;ten bağlayın
          </Link>
        </p>
      </div>
    );
  }

  if (graphQuery.error) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3">
        <AlertCircle className="h-8 w-8 text-danger" />
        <p className="text-sm text-text-secondary">Graph yüklenirken hata oluştu.</p>
        <button
          type="button"
          onClick={() => void graphQuery.refetch()}
          className="btn btn-secondary text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
        <GitBranch className="h-10 w-10 text-text-muted" />
        <p className="text-sm text-text-secondary">
          Henüz commit yok.
          <br />
          <span className="text-xs text-text-muted">Repo&apos;ya commit push edilince burası otomatik güncellenir.</span>
        </p>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Main 3-pane layout
  // ---------------------------------------------------------------------------
  return (
    <div
      className="flex gap-3"
      style={{ height: "calc(100vh - 220px)", minHeight: 480 }}
    >
      {/* Pane 1: Branch sidebar (~176px) */}
      <BranchSidebar
        branches={branches}
        selectedBranch={selectedBranch}
        onSelectBranch={handleBranchSelect}
      />

      {/* Pane 2: Commit list (relative — anchors the svg overlay + floating card) */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-hairline bg-surface">
        {/* Column header */}
        <div
          className="flex flex-shrink-0 items-center gap-3 border-b border-hairline bg-inset py-1.5 pr-4 text-[10px] font-semibold uppercase tracking-wider text-text-muted"
          style={{ paddingLeft: GUTTER_W }}
          aria-hidden="true"
        >
          <span className="w-[70px] flex-shrink-0">SHA</span>
          <span className="min-w-0 flex-1">Message</span>
          <span className="hidden w-24 flex-shrink-0 sm:block">Author</span>
          <span className="w-14 flex-shrink-0 text-right">Time</span>
        </div>

        {/* Scrollable commit list — overlay + rows + card share this scroll ctx */}
        <div
          role="list"
          aria-label="Commit history"
          className="relative flex-1 overflow-y-auto"
          onMouseDown={closeSelection /* click-away dismiss */}
        >
          {/* Loading branch commits */}
          {selectedBranch && branchCommitsQuery.isLoading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-text-muted" />
            </div>
          )}

          {displayCommits.length > 0 && (
            <>
              {/* Continuous lane overlay (behind the rows) */}
              <LaneOverlay
                displayCommits={displayCommits}
                laneOfSha={laneOfSha}
                selectedSha={selectedCommitSha}
                highlightedShas={highlightedShas}
              />

              {/* Rows */}
              <div className="relative">
                {displayCommits.map((commit) => (
                  <CommitRow
                    key={commit.sha}
                    commit={commit}
                    laneOfSha={laneOfSha}
                    isSelected={commit.sha === selectedCommitSha}
                    isNew={highlightedShas.has(commit.sha)}
                    boardKey={boardKey}
                    onClick={() => handleCommitClick(commit.sha)}
                  />
                ))}
              </div>

              {/* Floating glass detail card (quick-look) */}
              {selectedCommit && selectedRowIdx >= 0 && (
                <FloatingDetailCard
                  boardKey={boardKey}
                  commit={selectedCommit}
                  rowIdx={selectedRowIdx}
                  totalRows={displayCommits.length}
                  onViewDiff={() => setDiffOpen(true)}
                  onClose={closeSelection}
                />
              )}
            </>
          )}

          {selectedBranch && !branchCommitsQuery.isLoading && displayCommits.length === 0 && (
            <div className="flex h-32 items-center justify-center text-sm text-text-muted">
              Bu branch&apos;te commit yok.
            </div>
          )}
        </div>
      </div>

      {/* Pane 3: Commit diff panel (deep-dive — opens via "View diff") */}
      {selectedCommit && diffOpen && (
        <CommitDiffPanel
          boardKey={boardKey}
          sha={selectedCommit.sha}
          summary={selectedCommit.summary}
          onClose={() => setDiffOpen(false)}
        />
      )}
    </div>
  );
}
