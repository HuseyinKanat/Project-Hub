/**
 * focusTrap.test.ts — PH-305
 *
 * Locks the pure Tab/Shift+Tab wrap decision (`nextTrapTarget`) extracted from the
 * shared modal primitive (`ui/Modal`). Runs via Node's built-in test runner
 * (node:test + native TS strip), NOT the app tsc — the repo convention (see
 * grouping.test.ts, identityGuard.test.ts). The helper is import-free so this
 * relative import works without the Vite `@/` alias. Run:
 *   node --test --experimental-strip-types src/components/ui/focusTrap.test.ts
 *
 * The RUNTIME keyboard behaviour (real Tab cycle / Esc / backdrop / focus-return
 * on a live DOM) is verified by browser-manual QA smoke — no RTL/Playwright exists
 * — so this file only pins the branch-free decision the effect delegates to.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { nextTrapTarget } from "./focusTrap.ts";

test("empty list → null (nothing to trap)", () => {
  assert.equal(nextTrapTarget([], null, false), null);
  assert.equal(nextTrapTarget([], null, true), null);
});

test("single focusable pins focus to itself in both directions", () => {
  // fwd: active === last → first (itself); back: active === first → last (itself)
  assert.equal(nextTrapTarget(["only"], "only", false), "only");
  assert.equal(nextTrapTarget(["only"], "only", true), "only");
});

test("middle element → null (let native Tab move within the dialog)", () => {
  const els = ["a", "b", "c"];
  assert.equal(nextTrapTarget(els, "b", false), null);
  assert.equal(nextTrapTarget(els, "b", true), null);
});

test("wrap-forward: Tab on the last element wraps to first", () => {
  assert.equal(nextTrapTarget(["a", "b", "c"], "c", false), "a");
});

test("wrap-backward: Shift+Tab on the first element wraps to last", () => {
  assert.equal(nextTrapTarget(["a", "b", "c"], "a", true), "c");
});

test("no wrap when Tab is on first / Shift+Tab is on last (native step)", () => {
  assert.equal(nextTrapTarget(["a", "b", "c"], "a", false), null);
  assert.equal(nextTrapTarget(["a", "b", "c"], "c", true), null);
});

test("active element not in the list → null", () => {
  assert.equal(nextTrapTarget(["a", "b", "c"], "z", false), null);
  assert.equal(nextTrapTarget(["a", "b", "c"], "z", true), null);
});

test("active null (nothing focused yet) → null", () => {
  assert.equal(nextTrapTarget(["a", "b", "c"], null, false), null);
  assert.equal(nextTrapTarget(["a", "b", "c"], null, true), null);
});
