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

// PH-198 (S2): per-BRANCH color. `laneColor` keys by lane INDEX, so disjoint
// branches that recycle a lane number share a color (the PH-178..186 region all
// rendered one green). The branch-curve color must instead be stable per branch
// IDENTITY — keyed by the branch's tip sha (the newest commit on its span). We
// hash the key into the NON-lane-0 palette (lanes 1..N: skip index 0 = cyan,
// reserved for the default/main backbone) so a feature branch never collides
// with main and two distinct branches reusing a lane get DISTINCT colors.
// Deterministic (pure function of the sha) → stable across re-renders + theme
// switches (still returns a `var(--lane-*)` string, resolved at paint).
const BRANCH_PALETTE = LANE_COLORS.slice(1); // drop lane-0 cyan (= main).

/** FNV-1a 32-bit string hash — small, fast, deterministic, no deps. */
function hashKey(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Per-branch curve color, keyed by a stable branch identity (e.g. the branch
 * tip sha). Hashes into the non-main palette so the BRANCH curve color is a
 * function of WHICH branch it is, not which lane it happens to occupy (PH-198).
 */
export function branchColor(key: string): string {
  return BRANCH_PALETTE[hashKey(key) % BRANCH_PALETTE.length]!;
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
/**
 * Mutable lane-assignment state shared by the two passes. `nextLane` is boxed in
 * an object so the extracted helpers can bump it (the inline original used a
 * `let nextLane` closure variable). Behavior is identical to the inline code.
 */
interface LaneState {
  laneOfSha: Map<string, number>;
  laneActive: (string | null)[];
  nextLane: number;
}

/**
 * Bind `sha` to a lane: reuse the leftmost free slot if one exists, else
 * allocate `nextLane`. Grows `laneActive` and marks the slot occupied by `sha`.
 * Byte-for-byte the `findFreeLane → ?? nextLane++ → set → grow → occupy` block
 * the inline original repeated for the commit-self and merge-parent cases.
 */
function claimLaneForSha(st: LaneState, sha: string): number {
  const free = findFreeLane(st.laneActive);
  const lane = free ?? st.nextLane++;
  st.laneOfSha.set(sha, lane);
  while (st.laneActive.length <= lane) st.laneActive.push(null);
  st.laneActive[lane] = sha;
  return lane;
}

/** Pass 1 — seed lanes from branch heads (default branch → lane 0, others in
 *  name order get sequential lanes). Mutates `st` exactly as the inline pass 1. */
function seedBranchHeadLanes(st: LaneState, branches: GitBranchEntry[]): void {
  const defaultBranch = branches.find((b) => b.is_default);
  if (defaultBranch) {
    st.laneOfSha.set(defaultBranch.head_sha, 0);
    st.laneActive[0] = defaultBranch.head_sha;
    st.nextLane = 1;
  }

  const sorted = branches
    .filter((b) => !b.is_default)
    .sort((a, b) => a.name.localeCompare(b.name));

  for (const branch of sorted) {
    if (!st.laneOfSha.has(branch.head_sha)) {
      const lane = st.nextLane++;
      st.laneOfSha.set(branch.head_sha, lane);
      while (st.laneActive.length < lane) st.laneActive.push(null);
      st.laneActive[lane] = branch.head_sha;
    }
  }
}

/**
 * Propagate `commit`'s lane to its parents: first parent inherits the commit's
 * own lane; merge parents (index ≥ 1) claim a free/new lane. Identical to the
 * inline parent loop.
 */
function propagateToParents(st: LaneState, commit: GitCommitSummary, myLane: number): void {
  for (let i = 0; i < commit.parents.length; i++) {
    const parentSha = commit.parents[i]!;
    if (st.laneOfSha.has(parentSha)) continue;

    if (i === 0) {
      // First parent inherits same lane
      st.laneOfSha.set(parentSha, myLane);
      st.laneActive[myLane] = parentSha;
    } else {
      // Merge parents take a new/free lane
      claimLaneForSha(st, parentSha);
    }
  }
}

export function assignLanes(
  commits: GitCommitSummary[],
  branches: GitBranchEntry[],
): Map<string, number> {
  const st: LaneState = {
    laneOfSha: new Map<string, number>(),
    laneActive: [],
    nextLane: 0,
  };

  // Pass 1 — seed lanes from branch heads
  seedBranchHeadLanes(st, branches);

  // Pass 2 — walk commits newest-first
  for (const commit of commits) {
    if (!st.laneOfSha.has(commit.sha)) {
      claimLaneForSha(st, commit.sha);
    }

    const myLane = st.laneOfSha.get(commit.sha)!;

    // Propagate to parents
    propagateToParents(st, commit, myLane);

    // Free my lane slot if I've been fully resolved
    if (commit.parents.length === 0 && st.laneActive[myLane] === commit.sha) {
      st.laneActive[myLane] = null;
    }
  }

  return st.laneOfSha;
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
// PH-190 — contiguous-span, lane-0-anchored, per-row lane geometry emitter
//
// (Supersedes the PH-179 global-run + multi-row-bezier emitter.) Ports the
// ui_kit `Gutter` geometry (ui_kits/projecthub/branchgraph.jsx) EXACTLY into the
// single full-height overlay coordinate space. `computeLanePaths` is a pure,
// deterministic function over the *displayed* commits + the (frozen) laneOfSha
// map. It emits, in ONE shared coordinate space, PER ROW:
//   - straight vertical segments  only WITHIN a lane's contiguous active spans
//                                 (idle gaps on reused lanes are NEVER bridged)
//   - single-row fork curve        from main lane (0) at row TOP -> branch lane
//                                  at row MID, at a span's first row
//   - single-row merge curve       from branch lane at MID -> main lane (0) at
//                                  row BOTTOM, at a span's last row that merges
//   - dots                         one per commit, sitting exactly on its lane
//
// WHY THE REWRITE (the bug): the PH-179 emitter drew ONE vertical run per lane
// from the lane's GLOBAL first..last appearance. The REAL PH history reuses lane
// numbers (lane 1 is recycled by many short feature branches), so a global run
// bridged the IDLE rows between two disjoint branches that happen to share a
// lane number — the wiggly/tangled lanes the user reported. It also drew
// multi-row sweeping beziers across the full childY->parentY gap. Both only
// manifest on real data (the seeded mock has no lane reuse), which is why
// PH-179/PH-188 passed on mock but the user still saw wrong lanes.
//
// THE FIX: compute each lane's CONTIGUOUS active spans (maximal runs of
// consecutive active rows) and emit per-row segments anchored to lane 0 — a
// straight vertical inside a span, a single-row fork curve at the span start,
// a single-row merge curve at the span end (only when it merges onto main). No
// segment ever spans more than one row height; no segment ever bridges an idle
// gap. Pass-through verticals dim to 0.55 (own-lane stays 1.0), matching the
// kit `Gutter`.
//
// Coordinate space (KEPT — the laneW/2-CENTERED convention the dots use):
//   laneCx(lane) = gutterPad + min(lane,maxLanes-1)*laneW + laneW/2
//   y(rowIdx)    = rowIdx*rowH + rowH/2          (row center == this row's MID)
//   top(rowIdx)  = y(rowIdx) - rowH/2            (row TOP)
//   bottom(row)  = y(rowIdx) + rowH/2            (row BOTTOM)
//   height       = commits.length * rowH
//   gutterW      = maxLanes*laneW + gutterPad*2
//
// Kit control-point translation (kit row-local origin=row top, mid=ROW_H/2):
//   kit fork  `M lane0x 0 C lane0x mid*0.7, x mid*0.5, x mid`
//             -> `M x0 top C x0 (top+rowH*0.35), x (top+rowH*0.25), x mid`
//   kit merge `M x mid C x mid*1.5, lane0x mid*1.3, lane0x ROW_H`
//             -> `M x mid C x (top+rowH*0.75), x0 (top+rowH*0.65), x0 bottom`
//
// `assignLanes`/`laneColor`/`LANE_COLORS`/`computeMaxLane` above are UNTOUCHED —
// this is purely geometry derived from their output.
// ===========================================================================

/** A single drawn `<path>`/`<line>` element in the continuous lane overlay. */
export interface LaneSegment {
  /** lane color string (`var(--lane-*)`) — theme-aware, resolved at paint. */
  color: string;
  /** SVG path `d` attribute (vertical = `M..L..`, fork/merge curve = `M..C..`). */
  d: string;
  /**
   * stroke opacity — matches ui_kit `Gutter`: own-lane vertical 1.0,
   * pass-through (other lane) vertical 0.55, fork/merge curve 0.9.
   */
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

/**
 * PH-199 — an OPEN (unmerged) branch terminus marker. A side-lane span whose TIP
 * is NOT a parent of any lane-0 (main) commit (no merge commit references it) is
 * a dangling leaf — the branch never merged back. Instead of the closed
 * fork→merge loop a merged branch gets, an open branch tip is rendered as a
 * hollow open-ring "cap" so it reads clearly as "not merged" — distinct from a
 * merged branch's closed lane. The ring sits on the span TIP (newest commit on
 * its span) at its lane column.
 */
export interface OpenTip {
  cx: number;
  cy: number;
  r: number;
  color: string;
  sha: string;
}

/** Result of {@link computeLanePaths}: everything needed to paint the overlay. */
export interface LaneGeometry {
  segments: LaneSegment[];
  dots: LaneDot[];
  /**
   * PH-199 — open/unmerged branch tip markers (hollow ring caps). Empty when
   * every side-lane branch merged onto main. Painted by the overlay distinctly
   * from merged-branch geometry (which keeps its fork→merge loop).
   */
  openTips: OpenTip[];
  /** total gutter width in px (where rows must start their padding-left). */
  gutterW: number;
  /** total overlay height in px (= rows * rowH); the svg scrolls with rows. */
  height: number;
}

/**
 * Pure geometry emitter — see header. `commits` is the DISPLAYED list
 * (branch-filtered or full, newest-first); `laneOfSha` is the frozen full-graph
 * lane map (assignLanes). `maxLanes` clamps lane columns (lanes >= maxLanes
 * collapse onto the last column — preserves the component's MAX_LANES cap).
 *
 * Geometry: ported from the ui_kit `Gutter` — straight verticals within each
 * lane's contiguous active spans, single-row lane-0-anchored fork/merge curves
 * at span endpoints. NO global runs, NO multi-row beziers, NO idle-gap bridging.
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
  // Per-row top/mid/bottom in absolute overlay coordinates.
  const rowTop = (rowIdx: number): number => y(rowIdx) - rowH / 2;
  const rowBottom = (rowIdx: number): number => y(rowIdx) + rowH / 2;
  const laneOf = (sha: string): number =>
    Math.min(laneOfSha.get(sha) ?? 0, maxLanes - 1);

  const x0 = laneCx(0); // main-lane (lane 0) column center — the anchor.

  // sha -> row index in the DISPLAYED list (for first-parent span fill).
  const shaIndex = new Map<string, number>();
  commits.forEach((c, i) => shaIndex.set(c.sha, i));

  const segments: LaneSegment[] = [];
  const dots: LaneDot[] = [];
  const openTips: OpenTip[] = [];

  // --- PH-199: merged-vs-open signal from the parent DAG --------------------
  // A side-lane branch is MERGED iff its TIP sha is referenced as a parent by
  // some commit that sits on lane 0 (main) — i.e. a real merge commit on main
  // points AT the branch tip. This is ORTHOGONAL to "the branch forked off main"
  // (which `mergesOntoMain`'s old `laneOf(firstParent)===0` test conflated: that
  // is true for EVERY feature branch, merged or not). We collect the set of
  // shas referenced by any lane-0 commit's parents; a span tip in this set is
  // merged, one absent from it is OPEN (a dangling leaf → no merge-return curve,
  // gets the open-ring affordance instead). Derived purely from the parent SHAs
  // already in `commits` — no backend flag needed.
  const mergedTips = new Set<string>();
  commits.forEach((commit) => {
    if (laneOf(commit.sha) !== 0) return; // only lane-0 (main) commits merge in.
    for (const parentSha of commit.parents) {
      if (laneOf(parentSha) !== 0) mergedTips.add(parentSha); // side-lane parent.
    }
  });

  // --- Step 1: per-lane ACTIVE rows (contiguous-span source) ------------
  // A lane is "active" at a row if (a) the row's commit is ON that lane, OR
  // (b) the lane is a pass-through: a commit above is on `lane` and its FIRST
  // parent (same lane, by assignLanes' first-parent inheritance) is at/below
  // this row — so the lane is continuously occupied between a child row and
  // its first-parent row (inclusive). This fills the interior so a span is the
  // SAME extent assignLanes kept the lane occupied — and ONLY that extent
  // (idle gaps on reused lanes stay inactive => never bridged). THE fix.
  const activeRows = new Map<number, Set<number>>();
  const markActive = (lane: number, rowIdx: number): void => {
    let set = activeRows.get(lane);
    if (!set) {
      set = new Set<number>();
      activeRows.set(lane, set);
    }
    set.add(rowIdx);
  };

  commits.forEach((commit, rowIdx) => {
    const lane = laneOf(commit.sha);
    // (a) the commit's own row is active on its lane.
    markActive(lane, rowIdx);

    // (b) first-parent pass-through fill: if the first parent shares this lane
    // and is displayed BELOW this row, every row in between is active too.
    const firstParent = commit.parents[0];
    if (firstParent !== undefined) {
      const parentIdx = shaIndex.get(firstParent);
      if (
        parentIdx !== undefined &&
        parentIdx > rowIdx &&
        laneOf(firstParent) === lane
      ) {
        for (let r = rowIdx + 1; r <= parentIdx; r++) markActive(lane, r);
      }
    }
  });

  // --- Step 2: collapse each lane's active rows into CONTIGUOUS SPANS ---
  // span = [spanFirst, spanLast], a maximal run of consecutive active rows.
  // A lane reused by disjoint branches yields MULTIPLE spans (the regression
  // guard): there is no span that crosses an idle gap.
  // PH-198 (S2): each span carries a per-BRANCH color, keyed by the span's tip
  // commit sha (the commit at span.first — the newest/displayed-top row of the
  // span). Two disjoint spans reusing the same lane therefore have DIFFERENT
  // colors. Lane 0 (main backbone) stays lane-colored (cyan); only side-lane
  // branch curves + their in-span verticals use the per-branch color.
  interface Span {
    lane: number;
    first: number;
    last: number;
    color: string;
  }
  const spanAtRow = new Map<number, Map<number, Span>>(); // lane -> row -> span
  activeRows.forEach((rowSet, lane) => {
    const rowsSorted = [...rowSet].sort((a, b) => a - b);
    let start = rowsSorted[0]!;
    let prev = start;
    const flush = (last: number): void => {
      // lane 0 = main backbone → lane-colored; side lanes → per-branch color
      // keyed by the span's tip sha (stable per branch, not per lane index).
      const color =
        lane === 0 ? laneColor(0) : branchColor(commits[start]?.sha ?? `lane${lane}:${start}`);
      const span: Span = { lane, first: start, last, color };
      let byRow = spanAtRow.get(lane);
      if (!byRow) {
        byRow = new Map<number, Span>();
        spanAtRow.set(lane, byRow);
      }
      for (let r = span.first; r <= span.last; r++) byRow.set(r, span);
    };
    for (let k = 1; k < rowsSorted.length; k++) {
      const r = rowsSorted[k]!;
      if (r === prev + 1) {
        prev = r;
      } else {
        flush(prev);
        start = r;
        prev = r;
      }
    }
    flush(prev);
  });

  // PH-199: does THIS span's branch actually merge back onto main? The merge
  // signal is a property of the BRANCH (the span), not of any single row: the
  // span's TIP (`commits[span.first]` — the newest/displayed-top commit on the
  // span) must be a parent of some lane-0 commit (a real merge commit referencing
  // it → `mergedTips`). An open/unmerged branch's tip is a dangling leaf, absent
  // from `mergedTips` → it gets NO merge-return curve (drawn open instead).
  //
  // We key on the span TIP rather than the descent row's first-parent because the
  // descent curve is emitted at the span's LAST row (the branch base, where it
  // rejoins main), but whether the branch MERGED is determined by whether its TIP
  // is referenced by a merge commit. (For a single-commit branch first===last, so
  // tip === base, and both views coincide.)
  const branchMerges = (span: { lane: number; first: number }): boolean => {
    if (span.lane === 0) return false;
    const tipSha = commits[span.first]?.sha;
    return tipSha !== undefined && mergedTips.has(tipSha);
  };

  // --- Step 3: per-row, per-active-lane emission (port of kit Gutter) ----
  commits.forEach((commit, rowIdx) => {
    const cTop = rowTop(rowIdx);
    const cMid = y(rowIdx);
    const cBot = rowBottom(rowIdx);
    const ownLane = laneOf(commit.sha);

    // Every lane active at THIS row (from a span covering rowIdx).
    spanAtRow.forEach((byRow, lane) => {
      const span = byRow.get(rowIdx);
      if (!span) return; // lane idle here — emit NOTHING (no bridging).

      const x = laneCx(lane);

      // Lane 0 (main) backbone: always a straight vertical, opacity 1.0. It is
      // the anchor TO which others fork/merge, never a fork/merge itself.
      if (lane === 0) {
        segments.push({
          color: laneColor(0),
          d: `M ${x0} ${cTop} L ${x0} ${cBot}`,
          opacity: 1.0,
          kind: "lane",
        });
        return;
      }

      const isFirst = rowIdx === span.first;
      const isLast = rowIdx === span.last;
      const branchHue = span.color; // PH-198: per-branch, not laneColor(lane).
      const merged = branchMerges(span); // PH-199: real-merge (tip-is-parent) gate.

      // PH-198 (S1): the FORK/divergence descent (mid -> main bottom) for a
      // single-commit branch. Previously the `isFirst` block `return`ed before
      // the `isLast` descent block ever ran (a one-row span has isFirst===isLast),
      // so a single-commit branch drew ONLY the upward fork-out curve and its
      // divergence edge (down to the base-lane parent) was MISSING. We now emit
      // the descent in BOTH paths via this shared helper.
      //
      // PH-199: the descent (merge-return) curve is emitted ONLY when the branch
      // genuinely merged back (its TIP is a parent of a lane-0 commit). An open
      // branch instead gets a hollow open-ring cap on its TIP (emitted once, at
      // the span-first row) so it reads clearly as "not merged".
      const emitTerminus = (): void => {
        if (merged) {
          // MERGED: single-row curve mid -> main(bottom).
          // kit: `M x mid C x mid*1.5, lane0x mid*1.3, lane0x ROW_H`.
          segments.push({
            color: branchHue,
            d: `M ${x} ${cMid} C ${x} ${cTop + rowH * 0.75}, ${x0} ${cTop + rowH * 0.65}, ${x0} ${cBot}`,
            opacity: 0.9,
            kind: "merge",
          });
        }
      };

      // PH-199: an OPEN/unmerged branch gets a hollow open-ring cap on its TIP
      // (the span-first row) — the visible "not merged" affordance, distinct from
      // a merged branch's closed fork→merge lane. Emitted once per span at its
      // tip row, on the branch lane column.
      const emitOpenTipIfUnmerged = (): void => {
        if (!merged) {
          openTips.push({ cx: x, cy: cMid, r: 4.5, color: branchHue, sha: commit.sha });
        }
      };

      if (isFirst) {
        // branch-out: single-row curve main(top) -> this lane(mid).
        // kit: `M lane0x 0 C lane0x mid*0.7, x mid*0.5, x mid`
        segments.push({
          color: branchHue,
          d: `M ${x0} ${cTop} C ${x0} ${cTop + rowH * 0.35}, ${x} ${cTop + rowH * 0.25}, ${x} ${cMid}`,
          opacity: 0.9,
          kind: "branch",
        });
        emitOpenTipIfUnmerged(); // span tip is at the first row.
        if (isLast) {
          // SINGLE-ROW span (single-commit branch): the dot is this span's only
          // row, so its divergence/fork edge down to the base-lane parent lives
          // HERE. Emit the merge-return descent too (PH-198 S1) — but ONLY when
          // the branch genuinely merged (PH-199); otherwise it terminates open.
          emitTerminus();
        } else {
          // continue straight down within the span (kit `fb` line).
          segments.push({
            color: branchHue,
            d: `M ${x} ${cMid} L ${x} ${cBot}`,
            opacity: 1.0,
            kind: "lane",
          });
        }
        return;
      }

      if (isLast) {
        // straight from top to mid (the lane arrives at its terminal row)...
        segments.push({
          color: branchHue,
          d: `M ${x} ${cTop} L ${x} ${cMid}`,
          opacity: 1.0,
          kind: "lane",
        });
        emitTerminus();
        return;
      }

      // pass-through: straight vertical top -> bottom. own-lane 1.0, else 0.55.
      segments.push({
        color: branchHue,
        d: `M ${x} ${cTop} L ${x} ${cBot}`,
        opacity: lane === ownLane ? 1.0 : 0.55,
        kind: "lane",
      });
    });

    // Dot — one per commit, exactly on its lane column at the row center.
    // PH-198 (S2): a side-lane dot wears its BRANCH color (the span covering
    // this row on its own lane), so the dot matches its branch curve instead of
    // sharing a lane-index color with unrelated branches. Lane-0 dots stay cyan.
    const ownSpan = spanAtRow.get(ownLane)?.get(rowIdx);
    const dotColor =
      ownLane === 0 ? laneColor(0) : (ownSpan?.color ?? laneColor(ownLane));
    dots.push({
      cx: laneCx(ownLane),
      cy: cMid,
      r: commit.parents.length > 1 ? 5 : 4,
      color: dotColor,
      isMerge: commit.parents.length > 1,
      sha: commit.sha,
    });
  });

  return {
    segments,
    dots,
    openTips,
    gutterW: maxLanes * laneW + gutterPad * 2,
    height: commits.length * rowH,
  };
}
