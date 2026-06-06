/**
 * BranchGraph.tsx — PH-167 (SourceTree UX rework) · PH-175 (Cyan-on-Black) ·
 *                    PH-179 (continuous bezier lanes) ·
 *                    PH-188 (refit to ui_kit branchgraph.jsx — CSS-grid 3-pane)
 *
 * SourceTree/GitKraken-style vertical commit list with a CONTINUOUS lane gutter.
 *
 * PH-188 layout (authoritative target = `ui_kits/projecthub/branchgraph.jsx` +
 * `kit.css .bg-*`/`.diff-*` + screenshots 04-branch-graph[-commit-selected].png):
 *
 *   `.bg-wrap` is a CSS **grid** (NOT flex), edge-to-edge, NO gaps, NO per-pane
 *   rounded cards — only hairline dividers:
 *     grid-template-columns: 200px minmax(0,1fr) 340px   (commit selected)
 *     grid-template-columns: 200px minmax(0,1fr)         (.no-diff — none selected)
 *     border-top on the grid · border-right on sidebar · border-left on diff panel.
 *
 *   Pane 1  .bg-sidebar (200px) — "Branches" eyebrow, branch rows (dot+name+HEAD),
 *           bottom .bg-toolbar with a REAL "Ticketed only" filter toggle.
 *   Pane 2  .bg-list (bg-base) — .bg-list-head (38px) + scrollable .bg-rows.
 *           Rows are 44px; when a commit IS selected the list switches to
 *           COMPACT mode (Author + Time columns hidden — the 340px diff panel
 *           takes the room).
 *   Pane 3  .diff-panel (340px, border-left) — REPLACES PH-179's floating glass
 *           detail card. Selecting a commit opens this panel DIRECTLY (one
 *           selection state, no second `diffOpen` toggle). Header = mono sha +
 *           close X + summary title + "N files changed +add −del" (getCommit);
 *           body = <DiffViewer showSummary={false}> (panel header owns the stat,
 *           so DiffViewer's own summary is suppressed to avoid duplication).
 *
 * Invariants preserved: assignLanes/laneColor (frozen), continuous-bezier lanes
 * (computeLanePaths), git data fetching (getGraph/listCommits/getStatus), WS live
 * highlight (highlightedShas → fresh/animate-glowin + hollow dot), branch
 * filtering, lane-assignment correctness, keyboard a11y (role=list/listitem,
 * aria-pressed, focus-visible rings, Esc-to-close), theme-aware var(--lane-*).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, AlertCircle, Loader2, RefreshCw, X, Check } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { DiffViewer } from "@/components/diff";
import { cn } from "@/lib/utils";
import { relativeTime } from "@/lib/time";
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
/** Pixel width per lane column in the gutter SVG (kit Gutter LANE_W=15). */
const LANE_PX = 15;
/** Max lanes to render (cap width on repos with many branches). */
const MAX_LANES = 10;
/** Padding on left side of the gutter (kit GUT_PAD=12). */
const GUTTER_PAD = 12;
/** Data fetch limits */
const GRAPH_LIMIT = 150;
const BRANCH_LIMIT = 80;

/** Total gutter width (also the rows' padding-left). */
const GUTTER_W = MAX_LANES * LANE_PX + GUTTER_PAD * 2;

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
// CommitRow — single 44px row (NO per-row svg; gutter handled by overlay).
//
// kit `.bg-row` mapping: sha (70px, always text-accent, 12px mono) + message
// (flex, 13px text-primary + .bg-ref chips) + ticket key-chip + author (110px,
// COMPACT-hidden) + time (64px right, COMPACT-hidden). hover→bg-raised,
// selected→bg-accent-soft, fresh→animate-glowin (ph-glowin).
// ---------------------------------------------------------------------------

interface CommitRowProps {
  commit: GitCommitSummary;
  laneOfSha: Map<string, number>;
  isSelected: boolean;
  isNew: boolean;
  /** When a commit is selected the list goes compact (hide author + time). */
  compact: boolean;
  boardKey: string;
  onClick: () => void;
}

function CommitRow({
  commit,
  laneOfSha,
  isSelected,
  isNew,
  compact,
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
      onClick={onClick}
      className={cn(
        "relative flex w-full items-center gap-3 border-b border-hairline pr-4 text-left",
        "transition-colors hover:bg-raised",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
        isSelected && "bg-accent-soft",
        !isSelected && isNew && "animate-glowin",
      )}
      style={{ height: ROW_H, paddingLeft: GUTTER_W }}
    >
      {/* short sha — kit `.bg-sha`: always text-accent, mono, 12px */}
      <span
        className="w-[70px] flex-shrink-0 font-mono text-xs text-accent"
        title={commit.sha}
      >
        {commit.short_sha}
      </span>

      {/* summary + ref badges (kit `.bg-msg` flex, 13px text-primary) */}
      <span
        className="flex min-w-0 flex-1 items-center gap-2 text-[13px] text-text-primary"
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
                    "max-w-[80px] truncate rounded-[5px] px-1.5 py-px font-mono text-[10px] leading-none",
                    isHEAD
                      ? "border border-hairline-cyan bg-accent-soft text-accent"
                      : "border border-hairline bg-raised text-state-backlog",
                  )}
                  title={ref}
                >
                  {ref}
                </span>
              );
            })}
            {commit.refs.length > 2 && (
              <span className="rounded-[5px] bg-raised px-1 py-px text-[10px] text-text-muted">
                +{commit.refs.length - 2}
              </span>
            )}
          </span>
        )}
      </span>

      {/* ticket keys — kit `.key-chip` */}
      {commit.ticket_keys.length > 0 && (
        <span className="flex flex-shrink-0 items-center gap-1">
          {commit.ticket_keys.slice(0, 2).map((key) => (
            <Link
              key={key}
              to={`/boards/${boardKey}/tickets/${key}`}
              onClick={(e) => e.stopPropagation()}
              className="rounded-md border border-hairline-cyan bg-accent-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold text-accent hover:bg-accent-subtle focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
            >
              {key}
            </Link>
          ))}
        </span>
      )}

      {/* author — kit `.bg-author` 110px; hidden in compact mode */}
      {!compact && (
        <span className="w-[110px] flex-shrink-0 truncate text-xs text-text-secondary">
          {commit.author_name}
        </span>
      )}

      {/* time — kit `.bg-time` 64px right; hidden in compact mode */}
      {!compact && (
        <span className="w-16 flex-shrink-0 text-right text-[11px] text-text-muted">
          {relativeTime(commit.committed_at)}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// BranchSidebar — kit `.bg-sidebar` (200px grid track, border-right).
// "Branches" eyebrow + branch rows + bottom `.bg-toolbar` "Ticketed only"
// real-filter toggle.
// ---------------------------------------------------------------------------

interface BranchSidebarProps {
  branches: GitBranchEntry[];
  selectedBranch: string | null;
  onSelectBranch: (branch: string | null) => void;
  ticketedOnly: boolean;
  onToggleTicketedOnly: () => void;
}

function BranchSidebar({
  branches,
  selectedBranch,
  onSelectBranch,
  ticketedOnly,
  onToggleTicketedOnly,
}: BranchSidebarProps) {
  const sorted = [...branches].sort((a, b) => {
    if (a.is_default && !b.is_default) return -1;
    if (!a.is_default && b.is_default) return 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <aside
      aria-label="Branch list"
      className="flex flex-col gap-0.5 overflow-y-auto border-r border-hairline bg-surface px-2.5 py-3.5"
    >
      <h2 className="mb-1 px-1.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Branches
      </h2>

      {/* All branches */}
      <button
        type="button"
        aria-pressed={selectedBranch === null}
        onClick={() => onSelectBranch(null)}
        className={cn(
          "flex items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left text-[13px] transition-colors",
          "hover:bg-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          selectedBranch === null
            ? "bg-accent-soft font-semibold text-text-primary"
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
              "flex items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-left text-[13px] transition-colors",
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
              <span className="flex-shrink-0 rounded border border-hairline-cyan bg-accent-soft px-[5px] py-px font-mono text-[9px] font-semibold text-accent">
                HEAD
              </span>
            )}
          </button>
        );
      })}

      {branches.length === 0 && (
        <p className="px-1.5 text-[11px] text-text-muted">No branches</p>
      )}

      {/* Bottom toolbar — kit `.bg-toolbar` with "Ticketed only" real filter */}
      <div className="mt-auto border-t border-hairline pt-3.5">
        <button
          type="button"
          role="switch"
          aria-checked={ticketedOnly}
          onClick={onToggleTicketedOnly}
          className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-text-secondary transition-colors hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
        >
          <span
            className={cn(
              "grid h-[18px] w-[18px] flex-shrink-0 place-items-center rounded-[5px] border transition-colors",
              ticketedOnly
                ? "border-accent bg-accent-strong text-text-on-accent"
                : "border-hairline-strong bg-inset text-transparent",
            )}
            aria-hidden="true"
          >
            <Check className="h-3 w-3" strokeWidth={3} />
          </span>
          Ticketed only
        </button>
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// CommitDiffPanel — kit `.diff-panel` (right pane, 340px grid track,
// border-left). Header `.diff-head` = mono sha + close X + `.diff-title`
// summary + `.diff-stat` "N files changed +add −del" (from getCommit; cache
// shared with TicketCommits via queryKey ['git','commit',boardKey,sha]).
// Body = <DiffViewer showSummary={false}> (panel header owns the stat row).
// ---------------------------------------------------------------------------

interface CommitDiffPanelProps {
  boardKey: string;
  sha: string;
  summary: string;
  onClose: () => void;
}

function CommitDiffPanel({ boardKey, sha, summary, onClose }: CommitDiffPanelProps) {
  // Cache-shared with TicketCommits' CommitFiles + the prior FloatingDetailCard.
  const { data, isLoading, isError } = useQuery<GitCommitDetail>({
    queryKey: ["git", "commit", boardKey, sha],
    queryFn: () => api.git.getCommit(boardKey, sha),
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

  return (
    <aside
      role="complementary"
      aria-label="Commit diff panel"
      className="flex min-h-0 flex-col overflow-hidden border-l border-hairline bg-surface"
    >
      {/* `.diff-head` */}
      <div className="flex-shrink-0 border-b border-hairline px-4 py-3.5">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] text-text-muted">
            {sha.slice(0, 12)}
          </span>
          <button
            type="button"
            aria-label="Close diff panel"
            onClick={onClose}
            className="-mr-1 grid h-7 w-7 place-items-center rounded text-text-muted hover:bg-raised hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* `.diff-title` */}
        <p
          className="my-2 text-sm font-semibold leading-snug text-text-primary"
          title={summary}
        >
          {summary}
        </p>

        {/* `.diff-stat` — N files changed +add −del */}
        {!isError && (
          <div className="flex items-center gap-2.5 text-[13px] text-text-secondary">
            {isLoading || !stats ? (
              <span
                className="h-3.5 w-40 animate-pulse rounded bg-raised"
                aria-label="Loading commit stats"
              />
            ) : (
              <>
                <span>
                  <strong className="text-text-primary">{stats.files}</strong>{" "}
                  {stats.files === 1 ? "file" : "files"} changed
                </span>
                <span className="font-mono text-success">+{stats.adds}</span>
                <span className="font-mono text-danger">−{stats.dels}</span>
              </>
            )}
          </div>
        )}
      </div>

      {/* `.diff-body` — DiffViewer summary suppressed (panel header owns it) */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <DiffViewer fetch={{ kind: "commit", boardKey, sha }} showSummary={false} />
      </div>
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
  // Real filter (kit `.bg-toolbar` "Ticketed only"): show only ticketed commits.
  const [ticketedOnly, setTicketedOnly] = useState(false);

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

  const branchFiltered: GitCommitSummary[] = selectedBranch
    ? (branchCommitsQuery.data?.commits ?? [])
    : allCommits;

  // "Ticketed only" real filter — restrict to commits with ≥1 ticket key.
  // The lane gutter recomputes naturally over the reduced displayed set
  // (computeLanePaths takes the displayed list; off-list parents terminate
  // gracefully — branchGraphLayout R3). No orphaned lanes/dots.
  const displayCommits: GitCommitSummary[] = useMemo(
    () =>
      ticketedOnly
        ? branchFiltered.filter((c) => c.ticket_keys.length > 0)
        : branchFiltered,
    [branchFiltered, ticketedOnly],
  );

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
      setSelectedCommitSha((prev) => (prev === sha ? null : sha)); // re-click dismisses
      onCommitSelect?.(sha);
    },
    [onCommitSelect],
  );

  // Esc dismisses the selection (closes the diff panel + un-compacts the list).
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
  // Main layout — kit `.bg-wrap` CSS grid (no gap, border-top, hairline
  // dividers). `.no-diff` collapses the right track when nothing is selected.
  // ---------------------------------------------------------------------------
  const compact = Boolean(selectedCommit);

  return (
    <div
      className="grid border-t border-hairline"
      style={{
        gridTemplateColumns: selectedCommit
          ? "200px minmax(0,1fr) 340px"
          : "200px minmax(0,1fr)",
        height: "calc(100vh - 56px - 132px)",
        minHeight: 480,
      }}
    >
      {/* Pane 1: Branch sidebar (200px grid track, border-right) */}
      <BranchSidebar
        branches={branches}
        selectedBranch={selectedBranch}
        onSelectBranch={handleBranchSelect}
        ticketedOnly={ticketedOnly}
        onToggleTicketedOnly={() => setTicketedOnly((v) => !v)}
      />

      {/* Pane 2: Commit list (bg-base) — list-head + scrollable rows */}
      <div className="flex min-w-0 flex-col overflow-hidden bg-base">
        {/* `.bg-list-head` (38px) — column labels share row widths + compact rule */}
        <div
          className="flex h-[38px] flex-shrink-0 items-center gap-3 border-b border-hairline bg-inset pr-4 text-[11px] font-semibold uppercase tracking-wider text-text-muted"
          style={{ paddingLeft: GUTTER_W }}
          aria-hidden="true"
        >
          <span className="w-[70px] flex-shrink-0">SHA</span>
          <span className="min-w-0 flex-1">Message</span>
          {!compact && <span className="w-[110px] flex-shrink-0">Author</span>}
          {!compact && <span className="w-16 flex-shrink-0 text-right">Time</span>}
        </div>

        {/* Scrollable commit list — overlay + rows share this scroll context */}
        <div
          role="list"
          aria-label="Commit history"
          className="relative min-h-0 flex-1 overflow-y-auto"
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
                    compact={compact}
                    boardKey={boardKey}
                    onClick={() => handleCommitClick(commit.sha)}
                  />
                ))}
              </div>
            </>
          )}

          {/* Empty after branch filter */}
          {selectedBranch && !branchCommitsQuery.isLoading && branchFiltered.length === 0 && (
            <div className="flex h-32 items-center justify-center text-sm text-text-muted">
              Bu branch&apos;te commit yok.
            </div>
          )}

          {/* Empty after "Ticketed only" filter */}
          {ticketedOnly && branchFiltered.length > 0 && displayCommits.length === 0 && (
            <div className="flex h-32 items-center justify-center text-sm text-text-muted">
              Ticket bağlı commit yok.
            </div>
          )}
        </div>
      </div>

      {/* Pane 3: Diff panel (340px grid track, border-left) — opens directly */}
      {selectedCommit && (
        <CommitDiffPanel
          boardKey={boardKey}
          sha={selectedCommit.sha}
          summary={selectedCommit.summary}
          onClose={closeSelection}
        />
      )}
    </div>
  );
}
