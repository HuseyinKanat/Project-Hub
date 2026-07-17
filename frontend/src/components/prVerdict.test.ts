/**
 * prVerdict.test.ts — PRDEV-1
 *
 * Locks the pure PR-verdict contract the PrVerdictBadge / PrReviewPanel / TicketCard /
 * TicketDetail surfaces render from: label→verdict resolution (+ priority + alias +
 * unknown→null), the verdict-label filter, the comment severity/sonar parsers, and the
 * pr-reviewer handoff predicate (fed a REAL `parseCommentMarker` marker — the same reuse
 * the panel does). Runs via Node's built-in test runner (node:test + native TS strip),
 * NOT the app tsc — repo convention (see commentRoleChip.test.ts, CommentCard.test.ts).
 * Run:
 *   node --test --experimental-strip-types src/components/prVerdict.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  resolvePrVerdict,
  isPrVerdictLabel,
  parseVerdictCounts,
  parseSonarSummary,
  isPrReviewerHandoff,
} from "./prVerdict.ts";
// Reused verbatim by PrReviewPanel to find the pr-reviewer comment — imported via `.ts`
// (node:test resolves it; the app tsc excludes *.test.ts), so the predicate is pinned to
// the REAL marker shape, not a hand-rolled stub.
import { parseCommentMarker } from "./commentMarker.ts";

// --- resolvePrVerdict -------------------------------------------------------

test("each verdict label → its emoji + UPPERCASE label + theme colorVar", () => {
  assert.deepEqual(resolvePrVerdict(["pr_safe"]), {
    verdict: "safe",
    emoji: "✅",
    label: "SAFE",
    colorVar: "var(--success)",
  });
  assert.deepEqual(resolvePrVerdict(["pr_conditions"]), {
    verdict: "conditions",
    emoji: "⚠️",
    label: "CONDITIONS",
    colorVar: "var(--warning)",
  });
  assert.deepEqual(resolvePrVerdict(["pr_not_safe"]), {
    verdict: "not_safe",
    emoji: "❌",
    label: "NOT SAFE",
    colorVar: "var(--danger)",
  });
  assert.deepEqual(resolvePrVerdict(["pr_needs_discussion"]), {
    verdict: "needs_discussion",
    emoji: "🛑",
    label: "DISCUSSION",
    colorVar: "var(--lane-violet)",
  });
});

test("pr_changes_requested is an ALIAS onto not_safe (backward-compat)", () => {
  assert.equal(resolvePrVerdict(["pr_changes_requested"])?.verdict, "not_safe");
  assert.equal(resolvePrVerdict(["pr_changes_requested"])?.colorVar, "var(--danger)");
});

test("no verdict label → null (direct-mode / pre-review — regression-safe, AC6)", () => {
  assert.equal(resolvePrVerdict(null), null);
  assert.equal(resolvePrVerdict(undefined), null);
  assert.equal(resolvePrVerdict([]), null);
  assert.equal(resolvePrVerdict(["needs_revision", "qa_failed", "hotfix"]), null);
});

test("unknown pr_* token → null (never a fabricated badge)", () => {
  assert.equal(resolvePrVerdict(["pr_maybe"]), null);
  assert.equal(resolvePrVerdict(["pr_"]), null);
  assert.equal(resolvePrVerdict(["prsafe"]), null);
});

test("case + surrounding whitespace tolerated on the label", () => {
  assert.equal(resolvePrVerdict(["  PR_SAFE "])?.verdict, "safe");
  assert.equal(resolvePrVerdict(["Pr_Not_Safe"])?.verdict, "not_safe");
});

test(">1 verdict label → most-cautious wins (not_safe>discussion>conditions>safe, AC1)", () => {
  assert.equal(resolvePrVerdict(["pr_safe", "pr_not_safe"])?.verdict, "not_safe");
  assert.equal(resolvePrVerdict(["pr_conditions", "pr_needs_discussion"])?.verdict, "needs_discussion");
  assert.equal(resolvePrVerdict(["pr_safe", "pr_conditions"])?.verdict, "conditions");
  // order-independent
  assert.equal(resolvePrVerdict(["pr_not_safe", "pr_safe"])?.verdict, "not_safe");
});

test("verdict label sits among other labels — still resolves", () => {
  assert.equal(resolvePrVerdict(["hotfix", "pr_conditions", "backend"])?.verdict, "conditions");
});

// --- isPrVerdictLabel -------------------------------------------------------

test("isPrVerdictLabel true only for verdict tokens (incl. alias), else false", () => {
  for (const l of ["pr_safe", "pr_conditions", "pr_not_safe", "pr_needs_discussion", "pr_changes_requested"]) {
    assert.equal(isPrVerdictLabel(l), true, `${l} is a verdict label`);
  }
  assert.equal(isPrVerdictLabel("  PR_SAFE "), true); // case/whitespace-tolerant
  for (const l of ["needs_revision", "qa_failed", "hotfix", "pr_maybe", "", "backend"]) {
    assert.equal(isPrVerdictLabel(l), false, `${l} is NOT a verdict label`);
  }
});

test("resolvePrVerdict / isPrVerdictLabel agree — every filtered label is exactly the resolved one", () => {
  const labels = ["hotfix", "pr_not_safe", "needs_revision"];
  const filtered = labels.filter((l) => isPrVerdictLabel(l));
  assert.deepEqual(filtered, ["pr_not_safe"]);
  assert.equal(resolvePrVerdict(labels)?.verdict, "not_safe");
});

// --- parseVerdictCounts -----------------------------------------------------

test("severity counts parse from the documented N🔴 M🟠 K🟡 J🔵 shape", () => {
  const body = "[HANDOFF pr-reviewer→coordinator] verdict: 3🔴 / 2🟠 / 5🟡 / 1🔵 — see report";
  assert.deepEqual(parseVerdictCounts(body), { critical: 3, high: 2, medium: 5, low: 1 });
});

test("severity counts tolerate reversed emoji→number order and spacing", () => {
  assert.deepEqual(
    parseVerdictCounts("🔴 4, 🟠 0, 🟡 7, 🔵 2"),
    { critical: 4, high: 0, medium: 7, low: 2 },
  );
});

test("severity counts: missing severities default to 0 (partial body)", () => {
  assert.deepEqual(parseVerdictCounts("2🔴 findings, 1🟡 minor"), {
    critical: 2,
    high: 0,
    medium: 1,
    low: 0,
  });
});

test("parseVerdictCounts → null when no severity token is present (row hidden, AC3)", () => {
  assert.equal(parseVerdictCounts("no counts here"), null);
  assert.equal(parseVerdictCounts(""), null);
  assert.equal(parseVerdictCounts(null), null);
  assert.equal(parseVerdictCounts(undefined), null);
  // bare emoji with NO adjacent number is not a count
  assert.equal(parseVerdictCounts("🔴 critical issues found"), null);
});

// --- parseSonarSummary ------------------------------------------------------

test("sonar summary parses from sonar= / sonar: / SonarQube: (AC7)", () => {
  assert.equal(parseSonarSummary("sonar=0 new issues, gate OK"), "0 new issues, gate OK");
  assert.equal(parseSonarSummary("... sonar: 2 new code smells"), "2 new code smells");
  assert.equal(parseSonarSummary("SonarQube: gate PASSED (0 new)"), "gate PASSED (0 new)");
});

test("sonar summary stops at the line end, trims", () => {
  assert.equal(parseSonarSummary("sonar = clean\nnext line"), "clean");
});

test("parseSonarSummary → null when no sonar signal (row hidden)", () => {
  assert.equal(parseSonarSummary("no sonar mention token"), null); // no =/: after sonar
  assert.equal(parseSonarSummary("sonar="), null); // empty value
  assert.equal(parseSonarSummary(""), null);
  assert.equal(parseSonarSummary(null), null);
});

// --- isPrReviewerHandoff (fed a REAL parseCommentMarker marker) --------------

test("isPrReviewerHandoff true for a pr-reviewer HANDOFF marker (dash/underscore/space)", () => {
  for (const from of ["pr-reviewer", "pr_reviewer", "pr reviewer", "PR-Reviewer"]) {
    const { marker } = parseCommentMarker(`[HANDOFF ${from}→coordinator] changes requested`);
    assert.equal(isPrReviewerHandoff(marker), true, `from=${from}`);
  }
});

test("isPrReviewerHandoff false for other handoffs / non-handoff / null markers", () => {
  assert.equal(isPrReviewerHandoff(parseCommentMarker("[HANDOFF reviewer→qa] approved").marker), false);
  assert.equal(isPrReviewerHandoff(parseCommentMarker("[HANDOFF qa→done] tests pass").marker), false);
  assert.equal(isPrReviewerHandoff(parseCommentMarker("[BLOCKED] design discussion").marker), false);
  assert.equal(isPrReviewerHandoff(parseCommentMarker("plain comment, no marker").marker), false);
  assert.equal(isPrReviewerHandoff(null), false);
  assert.equal(isPrReviewerHandoff(undefined), false);
});
