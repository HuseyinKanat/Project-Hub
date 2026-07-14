/**
 * CommentCard.test.ts — PH-302
 *
 * Locks the pure `parseCommentMarker` contract that CommentCard renders from.
 * Runs via Node's built-in test runner (node:test + native TS strip), NOT the app
 * tsc — repo convention (see permissions.test.ts). Fixtures include REAL comment
 * bodies pulled from PH-297. Run:
 *   node --test --experimental-strip-types src/components/CommentCard.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { parseCommentMarker } from "./commentMarker.ts";

test("HANDOFF with Unicode arrow → captures from/to and strips the marker", () => {
  const { marker, body } = parseCommentMarker("[HANDOFF pm→architect] ticket created");
  assert.deepEqual(marker, { type: "HANDOFF", from: "pm", to: "architect" });
  assert.equal(body, "ticket created");
});

test("HANDOFF with ASCII arrow -> is tolerated", () => {
  const { marker, body } = parseCommentMarker("[HANDOFF frontend->reviewer] 3 commits");
  assert.deepEqual(marker, { type: "HANDOFF", from: "frontend", to: "reviewer" });
  assert.equal(body, "3 commits");
});

test("HANDOFF tolerates whitespace around the arrow and trims endpoints", () => {
  const { marker } = parseCommentMarker("[HANDOFF  qa  →  done ] tests 8/8");
  assert.deepEqual(marker, { type: "HANDOFF", from: "qa", to: "done" });
});

test("HANDOFF preserves a multi-line body after the marker line", () => {
  const { marker, body } = parseCommentMarker(
    "[HANDOFF reviewer→frontend] needs_revision\nFindings: 1 major\n- fix null guard",
  );
  assert.equal(marker?.type, "HANDOFF");
  assert.equal(body, "needs_revision\nFindings: 1 major\n- fix null guard");
});

test("BLOCKED fixed marker: no from/to, body follows", () => {
  const { marker, body } = parseCommentMarker("[BLOCKED] waiting on PH-296, needed: dispatch API");
  assert.deepEqual(marker, { type: "BLOCKED", from: null, to: null });
  assert.equal(body, "waiting on PH-296, needed: dispatch API");
});

test("RECOVERY / ESCALATION / EVIDENCE fixed markers parse by keyword", () => {
  assert.equal(parseCommentMarker("[RECOVERY] stale claim released").marker?.type, "RECOVERY");
  assert.equal(parseCommentMarker("[ESCALATION] permission issues").marker?.type, "ESCALATION");
  assert.equal(parseCommentMarker("[EVIDENCE] 3 run_ids attached").marker?.type, "EVIDENCE");
});

test("DEPLOY-FAILED wins over DEPLOY (longer keyword, not a dangling -FAILED)", () => {
  const { marker, body } = parseCommentMarker("[DEPLOY-FAILED] reverted 06fa325, root cause: 503");
  assert.deepEqual(marker, { type: "DEPLOY-FAILED", from: null, to: null });
  assert.equal(body, "reverted 06fa325, root cause: 503");
});

test("DEPLOY body keeps an in-text arrow untouched", () => {
  const { marker, body } = parseCommentMarker("[DEPLOY] merged 06fa325 → main + pushed origin");
  assert.deepEqual(marker, { type: "DEPLOY", from: null, to: null });
  assert.equal(body, "merged 06fa325 → main + pushed origin");
});

test("leading whitespace before the marker is tolerated", () => {
  const { marker, body } = parseCommentMarker("   [BLOCKED] later");
  assert.equal(marker?.type, "BLOCKED");
  assert.equal(body, "later");
});

test("marker-only comment yields an empty body (no trailing summary)", () => {
  const { marker, body } = parseCommentMarker("[EVIDENCE]");
  assert.equal(marker?.type, "EVIDENCE");
  assert.equal(body, "");
});

test("no marker: marker is null and body is byte-for-byte identical", () => {
  const raw = "just a plain human comment, no marker here.";
  const parsed = parseCommentMarker(raw);
  assert.equal(parsed.marker, null);
  assert.equal(parsed.body, raw);
});

test("a non-vocabulary bracket like [architect] is NOT a marker", () => {
  const raw = "[architect] design approved — technical_depth yazıldı";
  const parsed = parseCommentMarker(raw);
  assert.equal(parsed.marker, null);
  assert.equal(parsed.body, raw);
});

test("a similar-but-wrong keyword [DEPLOYED] does not match DEPLOY", () => {
  const parsed = parseCommentMarker("[DEPLOYED] to staging");
  assert.equal(parsed.marker, null);
  assert.equal(parsed.body, "[DEPLOYED] to staging");
});

// --- Real PH-297 comment bodies (fetched live from project-hub) -------------
test("real PH-297 HANDOFF body parses and strips correctly", () => {
  const real =
    "[HANDOFF pm→architect] ticket created — triage complete. Evidence-attachment feature (frontend Evidence UI, blocked_by PH-296).";
  const { marker, body } = parseCommentMarker(real);
  assert.deepEqual(marker, { type: "HANDOFF", from: "pm", to: "architect" });
  assert.ok(body.startsWith("ticket created — triage complete"));
  assert.ok(!body.includes("[HANDOFF"));
});

test("real PH-297 qa→done HANDOFF with newline summary", () => {
  const real =
    "[HANDOFF qa→done] PH-297 verified (iter-2) — 8/8 browser TC + unit 13/13.\nFix doğrulandı: upload formu artık render.";
  const { marker, body } = parseCommentMarker(real);
  assert.equal(marker?.type, "HANDOFF");
  assert.equal(marker?.from, "qa");
  assert.equal(marker?.to, "done");
  assert.ok(body.startsWith("PH-297 verified"));
});

test("real PH-297 DEPLOY body", () => {
  const real =
    "[DEPLOY] merged 06fa325 → main + pushed origin. Frontend docker vite HMR ile aldı (restart gerekmedi).";
  const { marker, body } = parseCommentMarker(real);
  assert.equal(marker?.type, "DEPLOY");
  assert.ok(body.startsWith("merged 06fa325 → main"));
});
