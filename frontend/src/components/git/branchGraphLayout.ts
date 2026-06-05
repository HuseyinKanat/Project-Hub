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
export const ROW_H = 36;   // px per commit row (compact row height)

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
