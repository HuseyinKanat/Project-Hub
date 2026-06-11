/**
 * branchGraphAssignLanes.test.ts — PH-265
 *
 * DIRECT regression guard for `assignLanes` (the lane-allocation core).
 *
 * --- The bug (PH-265) -------------------------------------------------------
 * `assignLanes` pass 2 freed a lane slot ONLY when `commit.parents.length === 0`
 * (a root commit). A real time-windowed git payload contains ~0 root commits, so
 * the recycle path was dead code: every merged feature branch claimed a fresh
 * monotonic lane via `nextLane++`. On the live PH board (150 commits, 64 merges,
 * 0 roots) this produced maxLane=66 / 67 distinct lanes, and the MAX_LANES=10
 * clamp crushed 73 of 150 commits onto the rightmost column — an unreadable pile.
 *
 * The fix dropped the `commit.parents.length === 0 &&` prefix from the pass-2
 * free guard so a lane is freed whenever `laneActive[myLane] === commit.sha`.
 * That is safe by construction: `propagateToParents` reassigns
 * `laneActive[myLane]` to an unplaced first parent when one exists (first-parent
 * inheritance), so the equality only holds when the branch genuinely
 * terminated/rejoined — freeing the slot for `findFreeLane` to recycle.
 *
 * --- Why a NEW file --------------------------------------------------------
 * All 13 prior branchGraph*.test.ts hand-build `laneOfSha` and only exercise
 * `computeLanePaths` geometry — none drive `assignLanes` itself, so the leak was
 * unguarded. These fixtures call the REAL `assignLanes` end-to-end (the path the
 * bug lived in) and assert the recycling + concurrency invariants directly.
 *
 * Test runner (PH-190 contract — NO vitest/jest dep; HOST Node 22.6+ / project
 * runs Node 26; the frontend Docker container is Node 20 and cannot strip types):
 *
 *   cd frontend && node --test --experimental-strip-types \
 *     src/components/git/branchGraphAssignLanes.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  assignLanes,
  computeLanePaths,
  computeMaxLane,
} from "./branchGraphLayout.ts";
import type { GitBranchEntry, GitCommitSummary } from "@/types/git";

// --- layout constants (mirror BranchGraph.tsx + the sibling layout tests) ---
const ROW_H = 44;
const LANE_W = 15;
const GUTTER_PAD = 12;
const MAX_LANES = 10;

/** Minimal commit factory — only the fields the algorithm reads matter. */
function commit(sha: string, parents: string[]): GitCommitSummary {
  return {
    sha,
    short_sha: sha.slice(0, 7),
    parents,
    author_name: "t",
    author_email: "t@e",
    authored_at: "2026-01-01T00:00:00Z",
    committed_at: "2026-01-01T00:00:00Z",
    summary: sha,
    is_conventional: false,
    commit_type: null,
    ticket_keys: [],
    refs: [],
  };
}

/** Minimal branch-head factory — `assignLanes` reads name/head_sha/is_default. */
function branch(name: string, headSha: string, isDefault = false): GitBranchEntry {
  return {
    name,
    head_sha: headSha,
    is_default: isDefault,
    ticket_key: null,
    ahead: 0,
    behind: 0,
  };
}

/** laneCx for the clamped last column (lane >= MAX_LANES collapse here). */
const laneCx = (lane: number): number =>
  GUTTER_PAD + Math.min(lane, MAX_LANES - 1) * LANE_W + LANE_W / 2;

// ===========================================================================
// (a) RECYCLING — a 0-root window of N sequential --no-ff merged feature
// branches must NOT leak a lane per branch. The freed-on-rejoin slot must be
// recycled so the whole history fits in ~2 lanes (main backbone + one reused
// side bump), with NOTHING piled on the clamp column.
//
// Synthetic backbone (N=12 branches, newest-first — the order the payload
// arrives in). For each k: a merge commit `Mk` on main whose FIRST parent is the
// previous main commit `M(k-1)` and whose SECOND parent is a single feature
// commit `fk`; `fk`'s only parent is that same previous main commit (i.e. the
// feature forked off main and was merged back via --no-ff). The window ends on a
// short main backbone whose oldest commit still has a parent OUTSIDE the window
// (base3) — so there are ZERO root commits, exactly like a real time slice.
//
//   row 0   M12  parents [M11, f12]   lane 0   merge of branch 12 onto main
//   row 1   f12  parents [M11]        lane 1   branch 12's single commit
//   row 2   M11  parents [M10, f11]   lane 0   merge of branch 11
//   row 3   f11  parents [M10]        lane 1   branch 11's single commit (REUSE)
//   ...                                        (f12 rejoined → lane 1 freed → reused)
//   row 23  f1   parents [base0]      lane 1
//   row 24  base0 parents [base1]     lane 0   main backbone (windowed, no root)
//   row 25  base1 parents [base2]     lane 0
//   row 26  base2 parents [base3]     lane 0   oldest in-window; base3 is OUTSIDE
//
// Before the fix: each fk leaks lane 1,2,3,... → maxLane=12, dots on the cap
// column once N exceeds MAX_LANES. After the fix: maxLane=1, 0 dots on the cap.
// ===========================================================================
test("(a) recycling: 0-root window of >=12 merged branches stays small, nothing on the clamp column", () => {
  const N = 12;
  const commits: GitCommitSummary[] = [];
  for (let k = N; k >= 1; k--) {
    const prevMain = k === 1 ? "base0" : `M${k - 1}`;
    commits.push(commit(`M${k}`, [prevMain, `f${k}`])); // merge commit on main
    commits.push(commit(`f${k}`, [prevMain])); // single feature commit, forked off prev main
  }
  // Trailing main backbone — windowed (oldest commit's parent base3 is OUTSIDE
  // the window) so the payload contains ZERO root commits, like real data.
  commits.push(commit("base0", ["base1"]));
  commits.push(commit("base1", ["base2"]));
  commits.push(commit("base2", ["base3"]));

  const branches: GitBranchEntry[] = [branch("main", `M${N}`, true)];

  const laneOfSha = assignLanes(commits, branches);

  // maxLane must reflect the number of CONCURRENTLY-active branches (here: one
  // side branch open at a time), NOT N. Pre-fix this was 12; post-fix it is 1.
  const maxLane = computeMaxLane(laneOfSha);
  assert.ok(
    maxLane <= 4,
    `recycled-lane window must stay small (<= 4); got maxLane=${maxLane} ` +
      `(pre-fix this leaks one lane per merged branch → maxLane=${N})`,
  );

  // The default branch head stays on lane 0 (the main backbone anchor).
  assert.equal(laneOfSha.get(`M${N}`), 0, "main head must stay on lane 0");

  // NOTHING may pile on the clamp column. computeLanePaths collapses lanes
  // >= MAX_LANES onto laneCx(MAX_LANES - 1); with maxLane small, no dot should
  // ever land there. Pre-fix, the leaked lanes >= 10 all clamp onto this column.
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  const clampCx = laneCx(MAX_LANES - 1); // laneCx(9) — the rightmost drawn column.
  const dotsOnClamp = geom.dots.filter((d) => d.cx === clampCx);
  assert.equal(
    dotsOnClamp.length,
    0,
    `no commit dot may be clamped onto the last column (cx=${clampCx}); ` +
      `got ${dotsOnClamp.length} piled there`,
  );
});

// ===========================================================================
// (b) CONCURRENCY invariants — three sub-cases on `assignLanes` directly.
// ===========================================================================

// --- (b1) TIME-OVERLAPPING branches get DISTINCT lanes. ---------------------
// Two branches A and B are open AT THE SAME TIME (B's merge sits above A's merge,
// and both branch tips appear before their shared fork point on main). While B is
// still open, A's lane must NOT be stolen for B — they need distinct lanes.
//
//   row 0   Mb  parents [Ma, b1]   lane 0   merge B onto main (B open across Ma)
//   row 1   Ma  parents [c0, a1]   lane 0   merge A onto main
//   row 2   b1  parents [c0]       lane ?   branch B tip
//   row 3   a1  parents [c0]       lane ?   branch A tip
//   row 4   c0  parents [c1]       lane 0   shared fork point on main
//   row 5   c1  parents [c2]       lane 0   main (windowed; c2 outside)
//
// b1 and a1 are both live simultaneously → they MUST occupy different lanes.
test("(b1) concurrency: two time-overlapping branches get DISTINCT lanes", () => {
  const commits = [
    commit("Mb", ["Ma", "b1"]),
    commit("Ma", ["c0", "a1"]),
    commit("b1", ["c0"]),
    commit("a1", ["c0"]),
    commit("c0", ["c1"]),
    commit("c1", ["c2"]),
  ];
  const laneOfSha = assignLanes(commits, [branch("main", "Mb", true)]);

  const laneA = laneOfSha.get("a1");
  const laneB = laneOfSha.get("b1");
  assert.ok(laneA !== undefined && laneB !== undefined, "both tips must be placed");
  assert.notEqual(
    laneA,
    laneB,
    `time-overlapping branches must occupy DISTINCT lanes; both got ${laneA}`,
  );
  // Neither sibling may sit on lane 0 (reserved for the main backbone).
  assert.notEqual(laneA, 0, "branch A must not steal the main lane");
  assert.notEqual(laneB, 0, "branch B must not steal the main lane");
  assert.equal(laneOfSha.get("Mb"), 0, "default branch head stays on lane 0");
});

// --- (b2) TIME-DISJOINT branches REUSE one lane. ----------------------------
// Branch B is born only AFTER branch A fully merged back to main. The slot A
// freed on rejoin must be recycled for B → both occupy the SAME lane.
//
//   row 0   Mb  parents [c1, b1]   lane 0   merge B onto main
//   row 1   b1  parents [c1]       lane 1   branch B tip
//   row 2   c1  parents [Ma]       lane 0   main (B forked here; A already merged)
//   row 3   Ma  parents [c0, a1]   lane 0   merge A onto main
//   row 4   a1  parents [c0]       lane 1   branch A tip (REUSES B's freed slot)
//   row 5   c0  parents [c2]       lane 0   main (windowed; c2 outside)
//
// A's lane is freed when its merge rejoins main, so when (older) A's tip is
// walked it reclaims the same lane B used → laneA === laneB.
test("(b2) concurrency: two time-disjoint branches REUSE one lane", () => {
  const commits = [
    commit("Mb", ["c1", "b1"]),
    commit("b1", ["c1"]),
    commit("c1", ["Ma"]),
    commit("Ma", ["c0", "a1"]),
    commit("a1", ["c0"]),
    commit("c0", ["c2"]),
  ];
  const laneOfSha = assignLanes(commits, [branch("main", "Mb", true)]);

  const laneA = laneOfSha.get("a1");
  const laneB = laneOfSha.get("b1");
  assert.ok(laneA !== undefined && laneB !== undefined, "both tips must be placed");
  assert.equal(
    laneA,
    laneB,
    `time-disjoint branches must REUSE one lane; A=${laneA} B=${laneB} ` +
      `(pre-fix the freed slot is never recycled → distinct leaked lanes)`,
  );
  assert.notEqual(laneA, 0, "side branches must not sit on the main lane");
});

// --- (b3) the default branch head maps to lane 0. ---------------------------
// Pass 1 seeds the default branch's head onto lane 0; a non-default branch head
// takes a sequential lane. This pins the "main backbone == lane 0" anchor that
// the whole geometry (and the per-branch color palette) depends on.
test("(b3) concurrency: the default branch head maps to lane 0", () => {
  const commits = [
    commit("d0", ["d1"]), // default head
    commit("d1", ["d2"]),
    commit("feat", ["d1"]), // non-default branch head
  ];
  const branches = [
    branch("main", "d0", true),
    branch("feature/x", "feat", false),
  ];
  const laneOfSha = assignLanes(commits, branches);

  assert.equal(laneOfSha.get("d0"), 0, "default branch head must be lane 0");
  assert.notEqual(
    laneOfSha.get("feat"),
    0,
    "a non-default branch head must NOT occupy lane 0",
  );
});
