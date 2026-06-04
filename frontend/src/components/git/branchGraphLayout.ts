/**
 * branchGraphLayout.ts — PH-159 (G10)
 *
 * Pure, side-effect-free, deterministic lane-assignment algorithm for
 * converting a GitGraphResponse into xyflow nodes + edges.
 *
 * Two-pass algorithm (O(N+E)):
 *   Pass 1 — seed lanes from branch heads (default branch → lane 0).
 *   Pass 2 — walk commits newest-first, inherit/propagate lanes to parents.
 *
 * Output is memoizable keyed on `commits.length + first.sha + last.sha`.
 */

import type { Edge, Node } from "@xyflow/react";
import type { GitBranchEntry, GitCommitSummary } from "@/types/git";

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------
export const COL_W = 220; // horizontal spacing between lanes (px)
export const ROW_H = 72;  // vertical spacing between commits (px)

// One color per lane (cyclic for > length)
const LANE_COLORS = [
  "#6366f1", // indigo   — default branch / lane 0
  "#22c55e", // green
  "#f59e0b", // amber
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#a78bfa", // violet
  "#f97316", // orange
  "#14b8a6", // teal
  "#e11d48", // rose
  "#84cc16", // lime
];

export function laneColor(lane: number): string {
  return LANE_COLORS[lane % LANE_COLORS.length]!;
}

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

export interface CommitNodeData extends Record<string, unknown> {
  sha: string;
  shortSha: string;
  summary: string;
  authorName: string;
  committedAt: string;
  refs: string[];
  ticketKeys: string[];
  lane: number;
  color: string;
  isNew: boolean; // true for 3-s highlight after WS git_synced
  boardKey: string;
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
      // Extend array if needed
      while (laneActive.length < lane) laneActive.push(null);
      laneActive[lane] = branch.head_sha;
    }
  }

  // Pass 2 — walk commits newest-first
  for (const commit of commits) {
    if (!laneOfSha.has(commit.sha)) {
      // Orphan commit — assign to a free lane or a new one
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

    // Free my lane slot if I've been fully resolved (no parents or all propagated)
    if (commit.parents.length === 0 && laneActive[myLane] === commit.sha) {
      laneActive[myLane] = null;
    }
  }

  return laneOfSha;
}

// ---------------------------------------------------------------------------
// buildNodesAndEdges — convert commits + laneMap to xyflow nodes/edges
// ---------------------------------------------------------------------------
export function buildNodesAndEdges(
  commits: GitCommitSummary[],
  branches: GitBranchEntry[],
  laneOfSha: Map<string, number>,
  highlightedShas: Set<string>,
  boardKey: string,
): { nodes: Node<CommitNodeData>[]; edges: Edge[] } {
  const shaIndex = new Map<string, number>();
  commits.forEach((c, idx) => shaIndex.set(c.sha, idx));

  const nodes: Node<CommitNodeData>[] = commits.map((commit, rowIdx) => {
    const lane = laneOfSha.get(commit.sha) ?? 0;
    const color = laneColor(lane);
    return {
      id: commit.sha,
      type: "commitNode",
      position: { x: lane * COL_W, y: rowIdx * ROW_H },
      data: {
        sha: commit.sha,
        shortSha: commit.short_sha,
        summary: commit.summary,
        authorName: commit.author_name,
        committedAt: commit.committed_at,
        refs: commit.refs ?? [],
        ticketKeys: commit.ticket_keys ?? [],
        lane,
        color,
        isNew: highlightedShas.has(commit.sha),
        boardKey,
      },
      draggable: false,
      selectable: true,
    };
  });

  const edges: Edge[] = [];
  for (const commit of commits) {
    for (let i = 0; i < commit.parents.length; i++) {
      const parentSha = commit.parents[i]!;
      const isTruncated = !shaIndex.has(parentSha);
      const myLane = laneOfSha.get(commit.sha) ?? 0;
      const parentLane = laneOfSha.get(parentSha) ?? myLane;
      const color = laneColor(myLane !== parentLane ? parentLane : myLane);

      edges.push({
        id: `${commit.sha}->${parentSha}`,
        source: commit.sha,
        target: isTruncated ? `${parentSha}__stub` : parentSha,
        type: "smoothstep",
        style: {
          stroke: color,
          strokeWidth: 1.5,
          opacity: isTruncated ? 0.3 : 0.8,
          strokeDasharray: isTruncated ? "4 4" : undefined,
        },
        animated: false,
        deletable: false,
        selectable: false,
      });
    }
  }

  return { nodes, edges };
}
