/**
 * commentRoleChip.test.ts — PH-307
 *
 * Locks the pure `resolveRoleChip` contract that CommentCard's RoleChip renders
 * from, AND pins the module's inlined fallback (the laneColor / hashString mirror)
 * to the SHARED originals so any palette or hash drift fails right here. Runs via
 * Node's built-in test runner (node:test + native TS strip), NOT the app tsc —
 * repo convention (see commentMarker CommentCard.test.ts / branchGraphLayout.test.ts).
 * Run:
 *   node --test --experimental-strip-types src/components/commentRoleChip.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { resolveRoleChip, CURATED_ROLES } from "./commentRoleChip.ts";
// The SHARED originals resolveRoleChip's fallback MIRRORS — imported via `.ts`
// (node:test resolves it; the app tsc excludes *.test.ts). This is the drift
// guard: if branchGraphLayout's palette or utils' hash ever changes, the
// assertions below diverge from the inlined copy and fail.
import { laneColor } from "./git/branchGraphLayout.ts";
import { hashString } from "../lib/utils.ts";

/** The 8 lane hues, from the SHARED laneColor — what the fallback must land on. */
const LANE_PALETTE = new Set(Array.from({ length: 8 }, (_, i) => laneColor(i)));

/** Expected deterministic fallback for a hint, computed from the shared helpers. */
function sharedFallback(hint: string): string {
  return laneColor(hashString(hint.trim().toLowerCase()) % 8);
}

// Every NEW server agent_role_hint (cli.py underscore-verbatim) with no curated
// hue must fall to the deterministic lane palette + slug label.
const NEW_UNDERSCORE_ROLES = [
  "unity_dev",
  "unity_scene_manager",
  "unity_platform",
  "android_dev",
  "ios_dev",
  "data_engineer",
  "data_labeler",
  "ml_engineer",
  "ml_analyst",
];

test("null / undefined / empty / whitespace hint → null (no chip)", () => {
  assert.equal(resolveRoleChip(null), null);
  assert.equal(resolveRoleChip(undefined), null);
  assert.equal(resolveRoleChip(""), null);
  assert.equal(resolveRoleChip("   "), null);
});

test("curated roles preserved — exact label + --role-* colorVar", () => {
  assert.deepEqual(resolveRoleChip("pm"), { label: "pm", colorVar: "var(--role-pm)" });
  assert.deepEqual(resolveRoleChip("architect"), { label: "arch", colorVar: "var(--role-architect)" });
  assert.deepEqual(resolveRoleChip("reviewer"), { label: "rev", colorVar: "var(--role-reviewer)" });
  assert.deepEqual(resolveRoleChip("qa"), { label: "qa", colorVar: "var(--role-qa)" });
  assert.deepEqual(resolveRoleChip("admin"), { label: "admin", colorVar: "var(--role-admin)" });
  assert.deepEqual(resolveRoleChip("orchestrator"), { label: "orch", colorVar: "var(--role-orchestrator)" });
  // `_dev` underscore hints + their bare aliases share the be/fe hue.
  assert.deepEqual(resolveRoleChip("backend_dev"), { label: "be", colorVar: "var(--role-backend)" });
  assert.deepEqual(resolveRoleChip("backend"), { label: "be", colorVar: "var(--role-backend)" });
  assert.deepEqual(resolveRoleChip("frontend_dev"), { label: "fe", colorVar: "var(--role-frontend)" });
  assert.deepEqual(resolveRoleChip("frontend"), { label: "fe", colorVar: "var(--role-frontend)" });
});

test("coordinator → orchestrator alias (curated hue; author chip only)", () => {
  assert.deepEqual(resolveRoleChip("coordinator"), {
    label: "orch",
    colorVar: "var(--role-orchestrator)",
  });
});

test("case + surrounding whitespace tolerated on curated lookup", () => {
  assert.deepEqual(resolveRoleChip("  PM "), { label: "pm", colorVar: "var(--role-pm)" });
  assert.deepEqual(resolveRoleChip("QA"), { label: "qa", colorVar: "var(--role-qa)" });
});

test("new / underscore roles → deterministic lane color + slug label (never muted)", () => {
  for (const role of NEW_UNDERSCORE_ROLES) {
    const chip = resolveRoleChip(role);
    assert.ok(chip, `${role} must produce a chip`);
    assert.ok(LANE_PALETTE.has(chip!.colorVar), `${role} → lane palette (got ${chip!.colorVar})`);
    assert.equal(chip!.label, role.replace(/_/g, " "));
    assert.ok(!chip!.label.includes("_"), `${role} label must be slugged`);
  }
});

test("fallback color MATCHES the shared laneColor(hashString % 8) — DRIFT GUARD", () => {
  // Pins the inlined palette + hash to the real branchGraphLayout / utils copies.
  for (const idx of [0, 1, 2, 3, 4, 5, 6, 7]) {
    // sanity: our expectation source is the SHARED palette in canonical order.
    assert.ok(LANE_PALETTE.has(laneColor(idx)));
  }
  for (const role of [...NEW_UNDERSCORE_ROLES, "totally_made_up_role", "xyz", "Zeta_9"]) {
    assert.equal(
      resolveRoleChip(role)!.colorVar,
      sharedFallback(role),
      `${role} fallback must equal the shared laneColor(hashString % 8)`,
    );
  }
});

test("determinism — same hint yields an identical chip across calls", () => {
  for (const role of ["unity_dev", "ml_analyst", "some_future_role"]) {
    assert.deepEqual(resolveRoleChip(role), resolveRoleChip(role));
    // and byte-identical colorVar specifically (the property under test).
    assert.equal(resolveRoleChip(role)!.colorVar, resolveRoleChip(role)!.colorVar);
  }
});

test("the fallback does NOT collapse to a single hue (the PH-307 fix)", () => {
  const hues = new Set(NEW_UNDERSCORE_ROLES.map((r) => resolveRoleChip(r)!.colorVar));
  assert.ok(hues.size >= 2, `expected multiple lane hues, got ${hues.size}`);
});

test("terminal handoff tokens stay OUT of CURATED_ROLES (→ muted mono in HandoffEndpoint)", () => {
  for (const terminal of ["done", "user", "coordinator", "epic"]) {
    assert.equal(CURATED_ROLES[terminal], undefined, `${terminal} must not be curated`);
  }
});
