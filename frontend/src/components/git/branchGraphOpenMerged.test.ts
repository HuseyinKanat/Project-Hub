/**
 * branchGraphOpenMerged.test.ts — PH-199 (FAILING reproduction, QA Mode A)
 *
 * Reproduces the PH-199 bug entirely in the pure geometry layer
 * (`computeLanePaths` in `branchGraphLayout.ts`). These tests are written to FAIL
 * on the CURRENT code and PASS once Dev fixes the merge-vs-open gate. They do NOT
 * touch prod code.
 *
 * --- The bug ---------------------------------------------------------------
 * An OPEN / unmerged branch — its tip is NOT reachable from main, and NO commit
 * on the base lane (lane 0) lists the branch tip among its parents (i.e. there is
 * no merge commit referencing it) — is rendered with the SAME closed fork→merge
 * geometry as a genuinely MERGED branch. Live example: PH-178's
 * `ph-178-delete-dead-slate-indigo-components` / `f3d25623` (never merged, ticket
 * stuck in_review) looks identical to PH-177 (`fd250d38` + merge `7e88e9a4`) /
 * PH-179 (`bf456a9b` + merge `c2708256`).
 *
 * --- Root cause (confirmed empirically before writing this test) -----------
 * `computeLanePaths`'s `mergesOntoMain(commit, lane)` returns true whenever the
 * branch tip's FIRST PARENT sits on lane 0:
 *
 *     return laneOf(firstParent) === 0;
 *
 * But a branch tip's first parent IS the divergence point on main for BOTH a
 * merged AND an open branch — every feature branch forks off main. So an open
 * branch (whose tip is a dangling leaf, referenced by NO lane-0 merge commit)
 * also passes this gate and gets a `kind:"merge"` descent curve back to main —
 * the closed fork→merge loop that makes it look merged. The TRUE merged signal is
 * orthogonal: some commit ON lane 0 must have the branch tip among ITS parents (a
 * merge commit pointing AT the tip). That signal is fully derivable from the
 * parent SHAs the layout already receives → FRONTEND-ONLY fix, no backend flag.
 *
 * --- Test vehicle ----------------------------------------------------------
 * Unit test (Node built-in `node:test` + native TS type-strip — the PH-190/PH-198
 * contract, NO vitest/jest dep). The layout INPUT (commits with parent SHAs +
 * laneOfSha) already carries enough to tell merged from open, so the bug is
 * reproducible here without any backend change:
 *
 *   cd frontend && node --test --experimental-strip-types \
 *     src/components/git/branchGraphOpenMerged.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { computeLanePaths } from "./branchGraphLayout.ts";
import type { GitCommitSummary } from "@/types/git";

// --- layout constants (mirror BranchGraph.tsx + the sibling layout tests) ---
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

/** All y-coordinates appearing in an `M..L..`/`M..C..` `d` string. */
function yCoords(d: string): number[] {
  const nums = d.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  const ys: number[] = [];
  for (let i = 1; i < nums.length; i += 2) ys.push(nums[i]!);
  return ys;
}

/** Does any `kind:"merge"` (descent-back-to-main) segment live on lane `lane`? */
function hasMergeCurveOnLane(
  geom: ReturnType<typeof computeLanePaths>,
  lane: number,
): boolean {
  const x = laneCx(lane);
  return geom.segments.some(
    (s) => s.kind === "merge" && s.d.includes(`${x} `),
  );
}

/** Is an open-ring affordance emitted for the span tip whose commit is `sha`? */
function hasOpenRingForSha(
  geom: ReturnType<typeof computeLanePaths>,
  sha: string,
): boolean {
  return geom.openTips.some((t) => t.sha === sha);
}

/**
 * PH-268 flag variant of `commit` — sets the backend `merged_into_default` flag.
 * When `flag` is undefined the field is omitted entirely (legacy/no-flag payload).
 */
function commitFlag(
  sha: string,
  parents: string[],
  flag: boolean | undefined,
): GitCommitSummary {
  const c = commit(sha, parents);
  if (flag !== undefined) c.merged_into_default = flag;
  return c;
}

// ===========================================================================
// FIXTURE — the live PH topology that triggers the bug, side by side:
//   - a MERGED branch (a lane-0 merge commit lists the branch tip as a parent)
//   - an OPEN branch (tip is a dangling leaf; NO lane-0 commit references it)
// both forked off the SAME main commit. Displayed newest-first:
//
//   row 0  Mmerged  parents [main1, mTip]  lane 0  merge commit (PH-177-like)
//   row 1  mTip      parents [fork]        lane 1  MERGED branch tip  -> IS a parent of Mmerged
//   row 2  oTip      parents [fork]        lane 2  OPEN branch tip     -> NOT a parent of any lane-0 commit
//   row 3  main1     parents [fork]        lane 0  main
//   row 4  fork      parents []            lane 0  main root / shared divergence point
//
// On lane 0, only `Mmerged` is a merge commit and it references `mTip` (merged).
// `oTip` is referenced by NOTHING on lane 0 — it is genuinely OPEN. Yet today
// `mergesOntoMain` keys ONLY off "tip's first parent (`fork`) is on lane 0",
// which is true for BOTH → both get a merge-return curve.
// ===========================================================================
function buildFixture(): {
  commits: GitCommitSummary[];
  laneOfSha: Map<string, number>;
} {
  const commits = [
    commit("Mmerged", ["main1", "mTip"]),
    commit("mTip", ["fork"]),
    commit("oTip", ["fork"]),
    commit("main1", ["fork"]),
    commit("fork", []),
  ];
  const laneOfSha = new Map<string, number>([
    ["Mmerged", 0],
    ["mTip", 1],
    ["oTip", 2],
    ["main1", 0],
    ["fork", 0],
  ]);
  return { commits, laneOfSha };
}

// ===========================================================================
// AC1 (PRIMARY REPRODUCTION) — an OPEN branch must NOT draw a merge-return
// curve. It currently DOES (same closed fork→merge loop as a merged branch) →
// this assertion FAILS on the current code.
// ===========================================================================
test("PH-199: open/unmerged branch draws NO merge-return curve (FAILS now)", () => {
  const { commits, laneOfSha } = buildFixture();
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  // oTip is on lane 2 and is referenced by NO lane-0 commit -> it is OPEN.
  const openHasMerge = hasMergeCurveOnLane(geom, 2);

  assert.equal(
    openHasMerge,
    false,
    "an OPEN/unmerged branch (tip is a dangling leaf — NOT a parent of any " +
      "lane-0 commit, no merge commit references it) must NOT emit a merge-return " +
      "(fork→merge closed-loop) curve back to main; today `mergesOntoMain` keys " +
      "only off 'the tip's first parent is on lane 0' (true for EVERY branch that " +
      "forks off main), so the open branch is drawn identically to a merged one. " +
      `lane-2 merge segments: ${JSON.stringify(
        geom.segments
          .filter((s) => s.kind === "merge" && s.d.includes(`${laneCx(2)} `))
          .map((s) => s.d),
      )}`,
  );
});

// ===========================================================================
// AC2 (the distinction must EXIST) — in the same graph, the MERGED branch keeps
// its merge-return curve while the OPEN branch does not. Today both have one, so
// the two are INDISTINGUISHABLE → this assertion FAILS now. After the fix:
// merged → has curve, open → no curve, so this passes.
// ===========================================================================
test("PH-199: merged vs open are visually distinct (merged has merge curve, open does not) (FAILS now)", () => {
  const { commits, laneOfSha } = buildFixture();
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  const mergedHasMerge = hasMergeCurveOnLane(geom, 1); // mTip — genuinely merged
  const openHasMerge = hasMergeCurveOnLane(geom, 2); // oTip — genuinely open

  // The merged branch SHOULD keep its closed fork→merge geometry (preserved
  // behavior, also asserted standalone in AC3).
  assert.equal(
    mergedHasMerge,
    true,
    "a genuinely MERGED branch (its tip IS a parent of a lane-0 merge commit) " +
      "must keep its merge-return curve",
  );

  // The DISTINCTION: merged != open. Today both are true → not distinct → FAIL.
  assert.notEqual(
    openHasMerge,
    mergedHasMerge,
    "an OPEN branch and a MERGED branch in the SAME graph must be drawn " +
      `distinctly, but both currently emit a merge-return curve (merged=${mergedHasMerge}, ` +
      `open=${openHasMerge}) — they are indistinguishable, exactly the PH-178 ` +
      "(open) vs PH-177/PH-179 (merged) symptom the user reported.",
  );
});

// ===========================================================================
// AC3 (REGRESSION GUARD — PASSES now and MUST keep passing) — a genuinely MERGED
// branch keeps its merge-return curve. This pins the second AC of the ticket:
// the fix must NOT strip the merge curve from real merges (no over-correction).
// It passes today (merges already get the curve) and after the fix.
// ===========================================================================
test("PH-199 regression: a genuinely merged branch KEEPS its merge-return curve (PASSES now and after fix)", () => {
  const { commits, laneOfSha } = buildFixture();
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  assert.equal(
    hasMergeCurveOnLane(geom, 1),
    true,
    "the merged branch (tip `mTip` IS a parent of lane-0 merge commit `Mmerged`) " +
      "must retain its fork→merge closed-loop geometry",
  );
});

// ===========================================================================
// AC4 (REGRESSION GUARD — PASSES now and MUST keep passing) — PH-190 geometry
// invariants survive: no segment spans more than one row height (no multi-row
// sweep / idle-gap bridge) and there is exactly one dot per commit. PH-190
// codewiki gotcha: "Lane geometry MUST be verified on REAL git history" — this
// cheap fixture pins the invariant; the Dev must ALSO confirm on live PH data
// (PH-178 open vs PH-177/PH-179 merged) per that gotcha.
// ===========================================================================
test("PH-199 regression: PH-190 invariants hold (one-row segments, dot-per-commit)", () => {
  const { commits, laneOfSha } = buildFixture();
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

// ===========================================================================
// PH-268 — backend `merged_into_default` flag path.
//
// MERGE-OF-MERGE FIXTURE — the live 54804385 shape: a side-lane branch tip whose
// merge commit lands on a NON-zero lane (a merge nested inside another merge
// region). The legacy lane-0-only `mergedTips` scan NEVER sees that merge commit,
// so it misses the side tip → false OPEN ring (the PH-268 bug). The backend flag
// fixes it: the merged side tip carries merged_into_default=true. Displayed
// newest-first:
//
//   row 0  Mouter  parents [main1, Minner]  lane 0  outer merge on main
//   row 1  Minner  parents [side1, sTip]    lane 2  INNER merge — NON-zero lane!
//   row 2  side1   parents [fork]           lane 1  outer feature branch commit
//   row 3  sTip    parents [fork]           lane 3  MERGED side tip (via Minner)
//   row 4  oTip    parents [fork]           lane 4  genuinely OPEN tip
//   row 5  main1   parents [fork]           lane 0  main
//   row 6  fork    parents []               lane 0  shared divergence point
//
// `sTip` is merged (reachable from main through Minner, which is on lane 2, NOT
// lane 0). `oTip` is a dangling leaf, never referenced. The legacy lane-0 scan
// would mark NEITHER sTip nor side1 (Minner is on lane 2, never scanned), so the
// flag path is the only one that closes sTip while keeping oTip open.
// ===========================================================================
function buildMergeOfMergeFixture(flags: {
  sTip: boolean | undefined;
  oTip: boolean | undefined;
  withFlags: boolean;
}): {
  commits: GitCommitSummary[];
  laneOfSha: Map<string, number>;
} {
  const f = (sha: string, parents: string[], flag: boolean): GitCommitSummary =>
    flags.withFlags ? commitFlag(sha, parents, flag) : commit(sha, parents);
  const commits = [
    // lane-0 / merge commits are themselves merged (their own flag true on the
    // flag path; irrelevant on the legacy path). Only the SIDE tips matter.
    f("Mouter", ["main1", "Minner"], true),
    f("Minner", ["side1", "sTip"], true),
    f("side1", ["fork"], true),
    flags.withFlags ? commitFlag("sTip", ["fork"], flags.sTip) : commit("sTip", ["fork"]),
    flags.withFlags ? commitFlag("oTip", ["fork"], flags.oTip) : commit("oTip", ["fork"]),
    f("main1", ["fork"], true),
    f("fork", [], true),
  ];
  const laneOfSha = new Map<string, number>([
    ["Mouter", 0],
    ["Minner", 2], // INNER merge on a NON-zero lane — the PH-268 trigger.
    ["side1", 1],
    ["sTip", 3],
    ["oTip", 4],
    ["main1", 0],
    ["fork", 0],
  ]);
  return { commits, laneOfSha };
}

// ---------------------------------------------------------------------------
// PH-268 AC1 (THE BUG) — a merged side tip whose merge commit is on a NON-zero
// lane (merge-of-merge) carries merged_into_default=true → NO open ring. The
// legacy lane-0 scan would draw a false open ring here; the flag closes it.
// ---------------------------------------------------------------------------
test("PH-268: merged tip with merge commit on a NON-zero lane (flag=true) draws NO open ring", () => {
  const { commits, laneOfSha } = buildMergeOfMergeFixture({
    sTip: true,
    oTip: false,
    withFlags: true,
  });
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  assert.equal(
    hasOpenRingForSha(geom, "sTip"),
    false,
    "a side-lane tip flagged merged_into_default=true whose merge commit (Minner) " +
      "landed on lane 2 (NOT lane 0) must NOT get an open ring — this is the PH-268 " +
      "54804385 false-positive: the legacy lane-0 mergedTips scan never sees Minner, " +
      `but the backend flag closes the tip. openTips: ${JSON.stringify(geom.openTips.map((t) => t.sha))}`,
  );
});

// ---------------------------------------------------------------------------
// PH-268 AC2 (no over-close) — a tip with merged_into_default=false in the SAME
// flag-bearing payload is STILL drawn open (genuine 7700a2f9 / stash shape).
// ---------------------------------------------------------------------------
test("PH-268: tip with merged_into_default=false is STILL drawn open (no over-close)", () => {
  const { commits, laneOfSha } = buildMergeOfMergeFixture({
    sTip: true,
    oTip: false,
    withFlags: true,
  });
  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  assert.equal(
    hasOpenRingForSha(geom, "oTip"),
    true,
    "a genuinely abandoned leaf flagged merged_into_default=false (the 7700a2f9 / " +
      "git-stash shape) must KEEP its open ring — the flag fix must not over-close " +
      `real open tips. openTips: ${JSON.stringify(geom.openTips.map((t) => t.sha))}`,
  );
  // And no merge-return curve for the open tip either.
  assert.equal(
    hasMergeCurveOnLane(geom, 4),
    false,
    "the merged_into_default=false tip must not emit a merge-return curve",
  );
});

// ---------------------------------------------------------------------------
// PH-268 AC3 (flag trusted over the window) — when the flag is present, the
// lane-0 scan is NOT consulted: the merged side tip closes even though the legacy
// derivation would have left it open. Cross-checks the two paths disagree here.
// ---------------------------------------------------------------------------
test("PH-268: flag overrides the legacy lane-0 inference (merge-of-merge is the divergence case)", () => {
  // Legacy (no flags): the lane-0 scan only sees Mouter (lane 0), whose parents
  // are main1 (lane 0) + Minner (lane 2). It adds Minner to mergedTips, NOT sTip.
  // sTip's span tip is sTip → absent → legacy would draw it OPEN (the bug).
  const legacy = buildMergeOfMergeFixture({
    sTip: undefined,
    oTip: undefined,
    withFlags: false,
  });
  const legacyGeom = computeLanePaths(
    legacy.commits,
    legacy.laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  assert.equal(
    hasOpenRingForSha(legacyGeom, "sTip"),
    true,
    "sanity: with NO flag the legacy lane-0 scan MISSES the merge-of-merge tip → " +
      "open ring (the pre-PH-268 bug behaviour the flag is designed to fix)",
  );

  // Flag path: same topology, sTip flagged merged → ring closed.
  const flagged = buildMergeOfMergeFixture({
    sTip: true,
    oTip: false,
    withFlags: true,
  });
  const flaggedGeom = computeLanePaths(
    flagged.commits,
    flagged.laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );
  assert.equal(
    hasOpenRingForSha(flaggedGeom, "sTip"),
    false,
    "with the backend flag the SAME merge-of-merge tip is correctly closed — the " +
      "flag is trusted over the window-local lane-0 inference",
  );
});

// ===========================================================================
// PH-268 LEGACY BACK-COMPAT — a payload with NO merged_into_default field on ANY
// commit reproduces the CURRENT lane-0 inference EXACTLY (graceful degradation on
// a stale/un-upgraded backend). Re-runs the original PH-199 fixture asserting the
// open/merged distinction is derived from the lane-0 parent scan, unchanged.
// ===========================================================================
test("PH-268 back-compat: no-flag payload falls back to lane-0 inference (open tip open, merged tip closed)", () => {
  const { commits, laneOfSha } = buildFixture(); // PH-199 fixture — NO flags set.
  // Confirm the fixture truly carries no flag (otherwise the fallback isn't tested).
  assert.ok(
    commits.every((c) => c.merged_into_default === undefined),
    "fixture must omit merged_into_default so the legacy fallback path is exercised",
  );

  const geom = computeLanePaths(
    commits,
    laneOfSha,
    ROW_H,
    LANE_W,
    GUTTER_PAD,
    MAX_LANES,
  );

  // mTip is referenced as a parent by lane-0 merge commit Mmerged → merged.
  assert.equal(
    hasOpenRingForSha(geom, "mTip"),
    false,
    "legacy fallback: a side tip referenced by a lane-0 merge commit is MERGED → no open ring",
  );
  assert.equal(
    hasMergeCurveOnLane(geom, 1),
    true,
    "legacy fallback: the merged side tip keeps its merge-return curve",
  );
  // oTip is referenced by no lane-0 commit → open.
  assert.equal(
    hasOpenRingForSha(geom, "oTip"),
    true,
    "legacy fallback: a dangling leaf (no lane-0 commit references it) stays open",
  );
});
