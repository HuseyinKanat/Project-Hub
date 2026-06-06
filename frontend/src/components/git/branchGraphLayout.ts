/**
 * branchGraphLayout.ts — PH-167 (SourceTree UX rework)
 *
 * Pure, side-effect-free, deterministic lane-assignment algorithm.
 * xyflow dependency removed (PH-167). Only lane math is exported;
 * rendering is done by BranchGraph.tsx with SVG gutter rows.
 *
 * Two-pass algorithm (O(N+E)):
 *   Pass 1 — seed lanes from branch heads (default branch → lane 0).
 *   Pass 2 — walk commits newest-first, inherit/propagate lanes to parents.
 */

import type { GitBranchEntry, GitCommitSummary } from "@/types/git";

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------
export const LANE_W = 16;  // px per lane column in SVG gutter
export const ROW_H = 44;   // px per commit row (PH-188: 40->44 to match ui_kit kit.css `.bg-row{height:44px}`)

// One color per lane (cyclic for > length).
// PH-175 (F6): theme-aware lane colors — values are `var(--lane-*)` reference
// strings (NOT hardcoded hex, NOT a JS theme branch). SVG `stroke`/`fill` and
// inline `style` resolve CSS custom properties at paint time, so the same string
// repaints when `html.light` redefines `--lane-*`. Order mirrors the design kit:
// lane 0 = cyan = accent = default branch. (F1 token contract, PH-170.)
const LANE_COLORS = [
  "var(--lane-cyan)",    // lane 0 — default branch (cyan = accent)
  "var(--lane-emerald)",
  "var(--lane-amber)",
  "var(--lane-rose)",
  "var(--lane-violet)",
  "var(--lane-sky)",
  "var(--lane-teal)",
  "var(--lane-indigo)",
];

export function laneColor(lane: number): string {
  return LANE_COLORS[lane % LANE_COLORS.length]!;
}

// ---------------------------------------------------------------------------
// findFreeLane — returns leftmost null slot, or null if none
// ---------------------------------------------------------------------------
function findFreeLane(laneActive: (string | null)[]): number | null {
  const idx = laneActive.indexOf(null);
  return idx === -1 ? null : idx;
}

// ---------------------------------------------------------------------------
// assignLanes — two-pass lane algorithm
// Returns Map<sha, laneIndex>
// ---------------------------------------------------------------------------
export function assignLanes(
  commits: GitCommitSummary[],
  branches: GitBranchEntry[],
): Map<string, number> {
  const laneOfSha = new Map<string, number>();
  const laneActive: (string | null)[] = [];
  let nextLane = 0;

  // Pass 1 — seed lanes from branch heads
  const defaultBranch = branches.find((b) => b.is_default);
  if (defaultBranch) {
    laneOfSha.set(defaultBranch.head_sha, 0);
    laneActive[0] = defaultBranch.head_sha;
    nextLane = 1;
  }

  const sorted = branches
    .filter((b) => !b.is_default)
    .sort((a, b) => a.name.localeCompare(b.name));

  for (const branch of sorted) {
    if (!laneOfSha.has(branch.head_sha)) {
      const lane = nextLane++;
      laneOfSha.set(branch.head_sha, lane);
      while (laneActive.length < lane) laneActive.push(null);
      laneActive[lane] = branch.head_sha;
    }
  }

  // Pass 2 — walk commits newest-first
  for (const commit of commits) {
    if (!laneOfSha.has(commit.sha)) {
      const free = findFreeLane(laneActive);
      const lane = free !== null ? free : nextLane++;
      laneOfSha.set(commit.sha, lane);
      while (laneActive.length <= lane) laneActive.push(null);
      laneActive[lane] = commit.sha;
    }

    const myLane = laneOfSha.get(commit.sha)!;

    // Propagate to parents
    for (let i = 0; i < commit.parents.length; i++) {
      const parentSha = commit.parents[i]!;
      if (laneOfSha.has(parentSha)) continue;

      if (i === 0) {
        // First parent inherits same lane
        laneOfSha.set(parentSha, myLane);
        laneActive[myLane] = parentSha;
      } else {
        // Merge parents take a new/free lane
        const free = findFreeLane(laneActive);
        const lane = free !== null ? free : nextLane++;
        laneOfSha.set(parentSha, lane);
        while (laneActive.length <= lane) laneActive.push(null);
        laneActive[lane] = parentSha;
      }
    }

    // Free my lane slot if I've been fully resolved
    if (commit.parents.length === 0 && laneActive[myLane] === commit.sha) {
      laneActive[myLane] = null;
    }
  }

  return laneOfSha;
}

// ---------------------------------------------------------------------------
// computeMaxLane — how many lanes are active across all commits
// ---------------------------------------------------------------------------
export function computeMaxLane(laneOfSha: Map<string, number>): number {
  let max = 0;
  for (const lane of laneOfSha.values()) {
    if (lane > max) max = lane;
  }
  return max;
}

// ===========================================================================
// PH-179 — continuous lane geometry emitter
//
// Replaces the per-row `<svg>` GutterCell (0->ROW_H segments, visible seams)
// with a single continuous full-height overlay. `computeLanePaths` is a pure,
// deterministic function over the *displayed* commits + the (frozen) laneOfSha
// map. It emits, in ONE shared coordinate space:
//   - vertical lane runs   (one path per lane spanning its full first..last row)
//   - cubic-bezier curves   for branch-in / merge connections (S-curve, matching
//                           the `M..C..` shape in branch-graph-row.html)
//   - dots                  one per commit, sitting exactly on its lane path
//
// Coordinate space (identical to what the component used for GutterCell):
//   laneCx(lane) = gutterPad + min(lane,maxLanes-1)*laneW + laneW/2
//   y(rowIdx)    = rowIdx*rowH + rowH/2          (row center)
//   height       = commits.length * rowH
//   gutterW      = maxLanes*laneW + gutterPad*2
//
// `assignLanes`/`laneColor`/`LANE_COLORS` above are UNTOUCHED — this is purely
// additive geometry derived from their output.
// ===========================================================================

/** A single drawn `<path>`/`<line>` element in the continuous lane overlay. */
export interface LaneSegment {
  /** lane color string (`var(--lane-*)`) — theme-aware, resolved at paint. */
  color: string;
  /** SVG path `d` attribute (vertical run = `M..L..`, curve = `M..C..`). */
  d: string;
  /** stroke opacity (0.55 active/own/curve, 0.18 pure pass-through). */
  opacity: number;
  /** semantic kind — drives nothing visual today but useful for tests/debug. */
  kind: "lane" | "branch" | "merge";
}

/** A single commit dot in the continuous lane overlay. */
export interface LaneDot {
  cx: number;
  cy: number;
  r: number;
  color: string;
  isMerge: boolean;
  sha: string;
}

/** Result of {@link computeLanePaths}: everything needed to paint the overlay. */
export interface LaneGeometry {
  segments: LaneSegment[];
  dots: LaneDot[];
  /** total gutter width in px (where rows must start their padding-left). */
  gutterW: number;
  /** total overlay height in px (= rows * rowH); the svg scrolls with rows. */
  height: number;
}

/**
 * Pure geometry emitter — see header. `commits` is the DISPLAYED list
 * (branch-filtered or full); `laneOfSha` is the frozen full-graph lane map
 * (assignLanes). `maxLanes` clamps lane columns (lanes >= maxLanes collapse
 * onto the last column — preserves the component's existing MAX_LANES cap).
 */
export function computeLanePaths(
  commits: GitCommitSummary[],
  laneOfSha: Map<string, number>,
  rowH: number,
  laneW: number,
  gutterPad: number,
  maxLanes: number,
): LaneGeometry {
  const laneCx = (lane: number): number => {
    const capped = Math.min(lane, maxLanes - 1);
    return gutterPad + capped * laneW + laneW / 2;
  };
  const y = (rowIdx: number): number => rowIdx * rowH + rowH / 2;
  const laneOf = (sha: string): number =>
    Math.min(laneOfSha.get(sha) ?? 0, maxLanes - 1);

  // sha -> row index in the displayed list (for parent lookup).
  const shaIndex = new Map<string, number>();
  commits.forEach((c, i) => shaIndex.set(c.sha, i));

  // --- Pass 1: lane extents (first/last row a lane appears on) ----------
  // Mirrors perRowActiveLanes' laneFirst/laneLast logic — one continuous run
  // per lane instead of stitched per-row segments.
  const laneFirst = new Map<number, number>();
  const laneLast = new Map<number, number>();
  commits.forEach((c, rowIdx) => {
    const lane = laneOf(c.sha);
    if (!laneFirst.has(lane)) laneFirst.set(lane, rowIdx);
    laneLast.set(lane, rowIdx);
  });

  const segments: LaneSegment[] = [];
  const dots: LaneDot[] = [];

  // --- Vertical lane runs (one per lane, full extent) -------------------
  laneFirst.forEach((first, lane) => {
    const last = laneLast.get(lane)!;
    if (first === last) return; // single-commit lane: no line, just the dot
    const cx = laneCx(lane);
    segments.push({
      color: laneColor(lane),
      d: `M ${cx} ${y(first)} L ${cx} ${y(last)}`,
      // lane 0 (main) reads as a solid backbone; others slightly dimmer.
      opacity: 0.55,
      kind: "lane",
    });
  });

  // --- Branch-in / merge bezier curves + dots ---------------------------
  commits.forEach((commit, rowIdx) => {
    const lane = laneOf(commit.sha);
    const childCx = laneCx(lane);
    const childY = y(rowIdx);
    const isMerge = commit.parents.length > 1;

    for (let pi = 0; pi < commit.parents.length; pi++) {
      const parentSha = commit.parents[pi]!;
      const parentLane = laneOf(parentSha);
      // first parent on the same lane is already covered by the vertical run.
      if (pi === 0 && parentLane === lane) continue;

      const parentCx = laneCx(parentLane);
      const parentIdx = shaIndex.get(parentSha);
      // R3: parent off the displayed list (branch-filtered view) — terminate
      // the curve gracefully one row below toward the parent lane column.
      const parentY =
        parentIdx !== undefined ? y(parentIdx) : childY + rowH;

      // Cubic bezier with vertical tangents at both ends => smooth S-curve,
      // matching `M44 20 C 44 40, 64 30, 64 50` in branch-graph-row.html.
      const c1y = childY + rowH * 0.5;
      const c2y = parentY - rowH * 0.5;
      const d = `M ${childCx} ${childY} C ${childCx} ${c1y}, ${parentCx} ${c2y}, ${parentCx} ${parentY}`;

      segments.push({
        // merge parents (pi>0) take the PARENT lane color; a first-parent lane
        // change (rebase/branch-in) takes the CHILD lane color.
        color: laneColor(pi === 0 ? lane : parentLane),
        d,
        opacity: 0.9,
        kind: pi === 0 ? "branch" : "merge",
      });
    }

    dots.push({
      cx: childCx,
      cy: childY,
      r: isMerge ? 5 : 4,
      color: laneColor(lane),
      isMerge,
      sha: commit.sha,
    });
  });

  return {
    segments,
    dots,
    gutterW: maxLanes * laneW + gutterPad * 2,
    height: commits.length * rowH,
  };
}
