/**
 * branchGraphFork.test.ts — PH-198 (FAILING reproduction, QA Mode A)
 *
 * Reproduces the two branch-graph regressions reported in PH-198, both rooted in
 * the pure geometry/color layer (`computeLanePaths` / `laneColor` in
 * `branchGraphLayout.ts`). These tests are written to FAIL on the current code
 * and PASS once Dev fixes the geometry + color mapping. They do NOT touch prod
 * code.
 *
 * --- Symptom 1: single-commit branch's FORK (divergence) edge is not drawn ---
 * A branch with exactly one commit on a side lane + a merge commit on main draws
 * only ONE lane-1 curve (the fork-out toward the merge at row TOP) and then
 * `return`s early because, for a single-row span, `isFirst === isLast === true`
 * and the `isFirst` branch in `computeLanePaths` returns before the `isLast`
 * merge/descent curve is ever considered. So NO segment connects the branch dot
 * (its row MID) DOWN to its base-lane parent (the divergence point, a lower row
 * on lane 0). The user: "tek commit içeren branchin ayrıldığı kısım çizilmiyor."
 *
 * Expected after fix: a single-commit branch emits curves on BOTH sides of its
 * dot — one curve reaching toward the merge above AND one curve descending to
 * the base-lane parent below (fork/divergence). Equivalently: lane-1 geometry
 * must occupy y-coordinates BELOW the branch dot's MID (toward its parent row),
 * not only at/above it.
 *
 * --- Symptom 2: per-lane (not per-branch) color reuse ------------------------
 * Curves are colored by `laneColor(lane) = LANE_COLORS[lane % len]`. Two disjoint
 * branches that recycle the SAME lane number (the PH-178..186 region) therefore
 * get the SAME color. The user: "her branch ayrı renkte olmalı." Expected after
 * fix: two distinct branches reusing one lane render in DISTINCT colors
 * (per-branch / per-merge-identity, not per-reused-lane).
 *
 * Test runner (PH-190 contract — NO vitest/jest dep; Node 22.6+ / project 26):
 *   cd frontend && node --test --experimental-strip-types \
 *     src/components/git/branchGraphFork.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { computeLanePaths, laneColor } from "./branchGraphLayout.ts";
import type { GitCommitSummary } from "@/types/git";

// --- layout constants (mirror BranchGraph.tsx + the sibling layout test) ---
const ROW_H = 44;
const LANE_W = 15;
const GUTTER_PAD = 12;
const MAX_LANES = 10;

/** Minimal commit factory — only the fields computeLanePaths reads matter. */
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

const laneCx = (lane: number): number =>
  GUTTER_PAD + Math.min(lane, MAX_LANES - 1) * LANE_W + LANE_W / 2;
const rowMid = (rowIdx: number): number => rowIdx * ROW_H + ROW_H / 2;

/** All y-coordinates appearing in an `M..L..`/`M..C..` `d` string. */
function yCoords(d: string): number[] {
  const nums = d.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const ys: number[] = [];
  for (let i = 1; i < nums.length; i += 2) ys.push(nums[i]!);
  return ys;
}

// ===========================================================================
// SYMPTOM 1 — single-commit branch: the FORK/divergence edge must be drawn.
//
// Displayed list (newest-first):
//   row 0  M   parents [c1, b1]   lane 0   merge of the branch back onto main
//   row 1  b1  parents [c1]       lane 1   the branch's ONLY commit (the tip)
//   row 2  c1  parents [c2]       lane 0   main: the DIVERGENCE point (b1's parent)
//   row 3  c2  parents []         lane 0   main root
//
// b1 sits at row 1 (mid y = 66) on lane 1. Its parent c1 sits at row 2 (mid
// y = 110) on lane 0 — that is where the branch DIVERGED. A correct graph
// connects b1's dot to c1: a fork/divergence curve descending from b1 (lane 1,
// y≈66) toward main below it (y > 66, toward row 2). On the CURRENT code, lane 1
// emits ONLY the top fork-out curve (`M x0 44 C ... x1 66`) whose y-coords are
// all <= the dot's mid (66); nothing descends below it → assertion FAILS.
// ===========================================================================
test("PH-198 S1: single-commit branch draws its fork/divergence edge (FAILS now)", () => {
  const commits = [
    commit("M", ["c1", "b1"]),
    commit("b1", ["c1"]),
    commit("c1", ["c2"]),
    commit("c2", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["M", 0],
    ["b1", 1],
    ["c1", 0],
    ["c2", 0],
  ]);

  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  const x1 = laneCx(1);
  const b1Mid = rowMid(1); // 66 — the branch dot's center.

  // Every lane-1 segment (branch lane). The branch dot is at y=66; the fork to
  // its parent (row 2) lives BELOW the dot. We assert that lane 1 reaches into
  // the region below its own dot — i.e. SOME lane-1 segment has a y strictly
  // greater than the dot's mid. Today the only lane-1 segment is the top
  // fork-out curve whose y-coords are all <= 66 → no descent → FAIL.
  const laneOneSegs = geom.segments.filter((s) => s.d.includes(`${x1} `));
  assert.ok(laneOneSegs.length >= 1, "expected at least one lane-1 segment");

  const reachesBelowDot = laneOneSegs.some((s) =>
    yCoords(s.d).some((yy) => yy > b1Mid + 0.5),
  );
  assert.equal(
    reachesBelowDot,
    true,
    "single-commit branch must draw a fork/divergence edge DESCENDING from its " +
      `dot (y=${b1Mid}) toward its base-lane parent below; current geometry only ` +
      "emits the upward merge/fork-out curve — the divergence segment is MISSING. " +
      `lane-1 segments: ${JSON.stringify(laneOneSegs.map((s) => s.d))}`,
  );
});

// ===========================================================================
// SYMPTOM 1 (multi-commit CONTRAST — PASSES now, isolates why single-commit
// breaks). A 2-commit branch ALREADY draws the descent toward its base-lane
// parent, because the branch base commit is the span's LAST row (isFirst !==
// isLast), so the `isLast` block runs and emits a `merge`-kind curve descending
// to main.
//   row 0  M   parents [c0, x2]   lane 0   merge
//   row 1  x2  parents [x1]       lane 1   branch tip   (span first)
//   row 2  x1  parents [c0]       lane 1   branch base  (span last → descent curve)
//   row 3  c0  parents []         lane 0   main: divergence point
// This test PASSES today and AFTER the fix — it isolates the root cause: the
// single-commit case (S1) breaks ONLY because isFirst===isLast short-circuits
// the early `return` in the `isFirst` branch before the `isLast` descent block.
// The fix must keep this multi-commit descent working while restoring the
// single-commit one (S1).
// ===========================================================================
test("PH-198 S1b: multi-commit branch base DOES draw its descent curve (PASSES; isolates S1 root cause)", () => {
  const commits = [
    commit("M", ["c0", "x2"]),
    commit("x2", ["x1"]),
    commit("x1", ["c0"]),
    commit("c0", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["M", 0],
    ["x2", 1],
    ["x1", 1],
    ["c0", 0],
  ]);

  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  const x1col = laneCx(1);
  const baseMid = rowMid(2); // x1 (the branch's base commit) dot center = 110.

  // A descending curve from x1's mid toward main below it (kind "merge").
  const descent = geom.segments.find(
    (s) =>
      s.d.includes(`${x1col} `) &&
      s.d.includes("C") &&
      yCoords(s.d).some((yy) => yy > baseMid + 0.5),
  );
  assert.ok(
    descent,
    "multi-commit branch base should already draw a descent curve toward its " +
      "base-lane parent (this works because isFirst!==isLast). lane-1 segments: " +
      JSON.stringify(
        geom.segments
          .filter((s) => s.d.includes(`${x1col} `))
          .map((s) => `${s.kind}:${s.d}`),
      ),
  );
});

// ===========================================================================
// SYMPTOM 2 — per-branch (not per-lane) color.
//
// Two disjoint single-commit branches both recycle lane 1 (the PH-178..186
// region pattern). They are DIFFERENT branches and must render in DISTINCT
// colors. Current code colors by lane index (`laneColor(1)` for both) → same
// color → assertion FAILS.
//
//   row 0  M0  parents [c1, b1]   lane 0   merge of branch A
//   row 1  b1  parents [c1]       lane 1   branch A tip
//   row 2  c1  parents [c2]       lane 0
//   row 3  c2  parents [c3]       lane 0   (idle on lane 1)
//   row 4  M1  parents [c3, b2]   lane 0   merge of branch B
//   row 5  b2  parents [c3]       lane 1   branch B tip
//   row 6  c3  parents []         lane 0   root
// ===========================================================================
test("PH-198 S2: two disjoint branches reusing a lane get DISTINCT colors (FAILS now)", () => {
  const commits = [
    commit("M0", ["c1", "b1"]),
    commit("b1", ["c1"]),
    commit("c1", ["c2"]),
    commit("c2", ["c3"]),
    commit("M1", ["c3", "b2"]),
    commit("b2", ["c3"]),
    commit("c3", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["M0", 0],
    ["b1", 1],
    ["c1", 0],
    ["c2", 0],
    ["M1", 0],
    ["b2", 1],
    ["c3", 0],
  ]);

  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  const x1 = laneCx(1);
  // Lane-1 curves are split into an upper region (branch A, rows 0-2) and a
  // lower region (branch B, rows 4-6); the idle band is around rowMid(3).
  const laneOneCurves = geom.segments.filter(
    (s) => s.kind !== "lane" && s.d.includes(`${x1} `),
  );
  const branchA = laneOneCurves.find((s) =>
    yCoords(s.d).every((yy) => yy < rowMid(3)),
  );
  const branchB = laneOneCurves.find((s) =>
    yCoords(s.d).every((yy) => yy > rowMid(3)),
  );
  assert.ok(branchA, "expected a lane-1 curve for branch A (upper region)");
  assert.ok(branchB, "expected a lane-1 curve for branch B (lower region)");

  assert.notEqual(
    branchA!.color,
    branchB!.color,
    "two DISTINCT branches that happen to reuse lane 1 must render in DISTINCT " +
      `colors (per-branch), but both are ${branchA!.color} because color is keyed ` +
      "by lane index (laneColor(1)). Expected per-branch coloring.",
  );
});

// ===========================================================================
// SYMPTOM 2 (root cause) — color must not be a pure function of lane index.
// Today `laneColor(1)` is deterministic regardless of which branch occupies the
// lane, so two distinct branches reusing lane 1 yield exactly ONE distinct
// branch-curve color. A per-branch fix yields >= 2 → assertion FAILS now.
// ===========================================================================
test("PH-198 S2-root: color must NOT be a pure function of lane index (FAILS now)", () => {
  const reusedLaneColor = laneColor(1);
  const commits = [
    commit("M0", ["c1", "b1"]),
    commit("b1", ["c1"]),
    commit("c1", ["c2"]),
    commit("c2", ["c3"]),
    commit("M1", ["c3", "b2"]),
    commit("b2", ["c3"]),
    commit("c3", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["M0", 0],
    ["b1", 1],
    ["c1", 0],
    ["c2", 0],
    ["M1", 0],
    ["b2", 1],
    ["c3", 0],
  ]);
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  const x1 = laneCx(1);
  const laneOneBranchColors = new Set(
    geom.segments
      .filter((s) => s.kind === "branch" && s.d.includes(`${x1} `))
      .map((s) => s.color),
  );
  assert.ok(
    laneOneBranchColors.size >= 2,
    `expected >= 2 distinct branch colors on reused lane 1 (per-branch), got ` +
      `${laneOneBranchColors.size}: ${JSON.stringify([...laneOneBranchColors])} ` +
      `(all == lane-index color ${reusedLaneColor})`,
  );
});

// ===========================================================================
// REGRESSION GUARD (PH-188/189/190) — the fix must NOT break the lane geometry
// invariants the prior tickets established (codewiki gotcha [PH-190]):
//   - no segment spans more than ONE row height (no multi-row sweep / idle bridge)
//   - one dot per commit
// This test PASSES now AND must keep passing after the fix (it pins the contract
// the Dev must preserve while adding the missing fork edge). PH-190 gotcha:
// "Lane geometry MUST be verified on REAL git history" — the live-data run via
// the app is the ultimate proof; this fixture pins the cheap invariants.
// ===========================================================================
test("PH-198 regression: geometry invariants (one-row segments, dot-per-commit) hold", () => {
  const commits = [
    commit("M", ["c1", "b1"]),
    commit("b1", ["c1"]),
    commit("c1", ["c2"]),
    commit("c2", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["M", 0],
    ["b1", 1],
    ["c1", 0],
    ["c2", 0],
  ]);
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  for (const s of geom.segments) {
    const ys = yCoords(s.d);
    const extent = Math.max(...ys) - Math.min(...ys);
    assert.ok(
      extent <= ROW_H * 1.001,
      `segment exceeds one row height (${ROW_H}): extent=${extent} d=${s.d}`,
    );
  }
  assert.equal(geom.dots.length, commits.length, "one dot per commit");
});
