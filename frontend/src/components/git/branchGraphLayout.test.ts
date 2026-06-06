/**
 * branchGraphLayout.test.ts — PH-190
 *
 * Regression guard for the contiguous-span, lane-0-anchored geometry rewrite of
 * `computeLanePaths`. The bug (PH-179): a single GLOBAL `laneFirst->laneLast`
 * vertical run per lane bridged the IDLE rows between two disjoint branches that
 * happen to reuse the same lane number — producing the wiggly/tangled lanes the
 * user saw ONLY on real PH history (the seeded mock had no lane reuse, so
 * PH-179/PH-188 passed on mock). These tests assert the new emitter:
 *   - splits a reused lane into SEPARATE contiguous spans (no idle-gap bridging);
 *   - emits single-row fork/merge curves anchored to main (lane 0) — no `M..C..`
 *     bezier ever spans more than one rowH in its y-extent;
 *   - keeps dots one-per-commit on the lane column.
 *
 * No test-runner dependency is added to package.json (forbidden). This file runs
 * under Node's built-in test runner with native TS type-stripping:
 *
 *   node --test --experimental-strip-types \
 *     src/components/git/branchGraphLayout.test.ts
 *
 * (Node >= 22.6; the project runs Node 26.)
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { computeLanePaths } from "./branchGraphLayout.ts";
import type { GitCommitSummary } from "@/types/git";

// --- layout constants (mirror BranchGraph.tsx) ---
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

/** Parse the y-coordinates out of an `M..C..`/`M..L..` `d` and return extent. */
function yExtent(d: string): number {
  // Numbers are `x y` pairs after M/C/L tokens; pull every 2nd number as y.
  const nums = d.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const ys: number[] = [];
  for (let i = 1; i < nums.length; i += 2) ys.push(nums[i]!);
  return Math.max(...ys) - Math.min(...ys);
}

// ---------------------------------------------------------------------------
// AC4 — reused-lane regression: lane 1 used by TWO disjoint branches with an
// idle gap in between. Must produce TWO spans; no segment bridges the gap.
// ---------------------------------------------------------------------------
//
// Displayed list (newest-first), lanes assigned by hand to force reuse:
//   row 0  M0   parents [c1, b1]   lane 0  (merge of branch A back to main)
//   row 1  b1   parents [c1]       lane 1  (branch A tip)        <- span A on L1
//   row 2  c1   parents [c2]       lane 0  (main)
//   row 3  c2   parents [c3]       lane 0  (main, IDLE on L1)    <- gap on L1
//   row 4  M1   parents [c3, b2]   lane 0  (merge of branch B back to main)
//   row 5  b2   parents [c3]       lane 1  (branch B tip)        <- span B on L1
//   row 6  c3   parents []         lane 0  (main, root)
//
// Lane 1 is active on rows {1} (span A) and {5} (span B): two disjoint spans
// separated by idle rows 2,3,4. The old global run would draw L1 from row 1 ->
// row 5, bridging the gap. The new emitter must NOT.
test("AC4 reused lane 1 -> two disjoint spans, no idle-gap bridge", () => {
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

  const laneOneCx = GUTTER_PAD + 1 * LANE_W + LANE_W / 2;
  const yOf = (row: number) => row * ROW_H + ROW_H / 2;
  // The idle rows on lane 1 are rows 2,3,4 → their y-MID band.
  const idleTop = yOf(2) - ROW_H / 2; // top of row 2
  const idleBot = yOf(4) + ROW_H / 2; // bottom of row 4

  // No lane-1 vertical segment may have BOTH endpoints straddling the idle band
  // (i.e. start above row 2 and end below row 4) — that would be a bridge.
  const laneOneVerticals = geom.segments.filter(
    (s) =>
      s.kind === "lane" &&
      s.d.includes(`M ${laneOneCx} `) &&
      s.d.includes("L"),
  );
  for (const seg of laneOneVerticals) {
    const ys = seg.d.match(/-?\d+(?:\.\d+)?/g)!.map(Number).filter((_, i) => i % 2 === 1);
    const segTop = Math.min(...ys);
    const segBot = Math.max(...ys);
    const bridges = segTop <= idleTop && segBot >= idleBot;
    assert.equal(
      bridges,
      false,
      `lane-1 vertical ${seg.d} bridges the idle gap [${idleTop},${idleBot}]`,
    );
  }

  // And concretely: lane 1 must have at least one segment near row 1 and one
  // near row 5, but none whose span covers the whole row1..row5 range.
  const anyFullBridge = geom.segments.some((s) => yExtent(s.d) > ROW_H * 1.001);
  assert.equal(
    anyFullBridge,
    false,
    "no segment may span more than one row height (AC2/AC4)",
  );
});

// ---------------------------------------------------------------------------
// AC2 — single-row fork + merge curves anchored to main (lane 0).
// A branch that forks from and merges back to main:
//   row 0  M   parents [c1, b1]   lane 0  (merge onto main)
//   row 1  b1  parents [c1]       lane 1  (branch tip; first row of span)
//   row 2  c1  parents []         lane 0  (main root)
// Lane 1 is a single-row span at row 1 (spanFirst===spanLast). Its first parent
// c1 is on lane 0 -> a fork curve should anchor to main. Every curve's y-extent
// must be <= one rowH.
// ---------------------------------------------------------------------------
test("AC2 fork/merge curves are single-row and anchored to lane 0", () => {
  const commits = [
    commit("M", ["c1", "b1"]),
    commit("b1", ["c1"]),
    commit("c1", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["M", 0],
    ["b1", 1],
    ["c1", 0],
  ]);

  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  const curves = geom.segments.filter((s) => s.d.includes("C"));
  assert.ok(curves.length >= 1, "expected at least one fork/merge curve");
  for (const c of curves) {
    assert.ok(
      yExtent(c.d) <= ROW_H * 1.001,
      `curve ${c.d} y-extent ${yExtent(c.d)} exceeds one rowH (${ROW_H})`,
    );
  }

  // Every fork ("branch") / merge curve must touch the main lane column (x0).
  const x0 = GUTTER_PAD + LANE_W / 2;
  for (const c of curves) {
    assert.ok(
      c.d.includes(`${x0} `),
      `curve ${c.d} is not anchored to main lane x0=${x0}`,
    );
  }
});

// ---------------------------------------------------------------------------
// AC5/AC7 — dot-count == row-count (no orphaned/missing dots), one per commit.
// ---------------------------------------------------------------------------
test("AC5/AC7 one dot per commit, on its lane column", () => {
  const commits = [
    commit("a", ["b"]),
    commit("b", ["c"]),
    commit("c", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["a", 0],
    ["b", 0],
    ["c", 0],
  ]);
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  assert.equal(geom.dots.length, commits.length, "dot-count must == row-count");
  const x0 = GUTTER_PAD + LANE_W / 2;
  for (const d of geom.dots) {
    assert.equal(d.cx, x0, "lane-0 dot must sit on main column");
  }
});

// ---------------------------------------------------------------------------
// AC3 — pass-through dimming: a lane passing through a row that is NOT its own
// commit lane renders at opacity 0.55; the own/main lane stays 1.0.
//   row 0  m0  parents [b1]   lane 1  (branch tip; span L1 starts)
//   row 1  c0  parents [c1]   lane 0  (main commit; L1 passes through here)
//   row 2  b1  parents []     lane 1  (branch parent; span L1 ends)
// At row 1 the own lane is 0 but lane 1 passes through -> its vertical is 0.55.
// ---------------------------------------------------------------------------
test("AC3 pass-through (other lane) vertical dims to 0.55", () => {
  const commits = [
    commit("m0", ["b1"]),
    commit("c0", ["c1"]),
    commit("b1", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["m0", 1],
    ["c0", 0],
    ["b1", 1],
  ]);
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  const laneOneCx = GUTTER_PAD + 1 * LANE_W + LANE_W / 2;
  const yMidRow1 = 1 * ROW_H + ROW_H / 2;
  const topRow1 = yMidRow1 - ROW_H / 2;
  const botRow1 = yMidRow1 + ROW_H / 2;
  // The lane-1 pass-through vertical covering row 1.
  const passThrough = geom.segments.find(
    (s) =>
      s.kind === "lane" &&
      s.d === `M ${laneOneCx} ${topRow1} L ${laneOneCx} ${botRow1}`,
  );
  assert.ok(passThrough, "expected a lane-1 pass-through vertical at row 1");
  assert.equal(passThrough!.opacity, 0.55, "pass-through other-lane must be 0.55");
});
