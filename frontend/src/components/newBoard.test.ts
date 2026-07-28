/**
 * newBoard.test.ts — PH-332
 *
 * Locks the pure New-board create-form logic that backs the NewBoardDialog:
 * client validation (mirrors ONLY the server's length constraints — no invented
 * key regex), the submit-enable gate, the create-payload builder, and the
 * status-code → form-error mapping (409 key / 403 permission / 422 field-map).
 * Runs via Node's built-in test runner (node:test + native TS strip), NOT the app
 * tsc — the repo convention (validation.test.ts, permissions.test.ts,
 * grouping.test.ts). Run:
 *   node --test --experimental-strip-types src/components/newBoard.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BOARD_KEY_MAX,
  DEFAULT_PROJECT_TYPE,
  isBoardSubmittable,
  mapApiErrorToForm,
  mapValidationBody,
  normalizeBoardKey,
  toBoardCreatePayload,
  validateBoardForm,
  type BoardFormFields,
} from "./newBoard.ts";

const base: BoardFormFields = {
  key: "AB",
  name: "Alpha Board",
  description: "",
  project_type: DEFAULT_PROJECT_TYPE,
};

// ---------------------------------------------------------------------------
// validateBoardForm — length-only mirror of BoardCreate
// ---------------------------------------------------------------------------

test("validateBoardForm: a valid form yields no errors", () => {
  assert.deepEqual(validateBoardForm(base), {});
});

test("validateBoardForm: key required + max 5", () => {
  assert.ok(validateBoardForm({ ...base, key: "" }).key);
  assert.ok(validateBoardForm({ ...base, key: "TOOLONG" }).key);
  assert.equal(validateBoardForm({ ...base, key: "ABCDE" }).key, undefined);
});

test("validateBoardForm: name required + max 160", () => {
  assert.ok(validateBoardForm({ ...base, name: "   " }).name);
  assert.ok(validateBoardForm({ ...base, name: "x".repeat(161) }).name);
});

test("validateBoardForm: description/project_type length caps", () => {
  assert.ok(validateBoardForm({ ...base, description: "d".repeat(2001) }).description);
  assert.ok(validateBoardForm({ ...base, project_type: "p".repeat(41) }).project_type);
});

// ---------------------------------------------------------------------------
// isBoardSubmittable — the submit-enable gate
// ---------------------------------------------------------------------------

test("isBoardSubmittable: needs non-empty key(≤5) + name", () => {
  assert.equal(isBoardSubmittable(base), true);
  assert.equal(isBoardSubmittable({ ...base, key: "" }), false);
  assert.equal(isBoardSubmittable({ ...base, name: "  " }), false);
  assert.equal(isBoardSubmittable({ ...base, key: "ABCDEF" }), false);
});

// ---------------------------------------------------------------------------
// normalizeBoardKey + toBoardCreatePayload
// ---------------------------------------------------------------------------

test("normalizeBoardKey uppercases", () => {
  assert.equal(normalizeBoardKey("ab"), "AB");
});

test("toBoardCreatePayload trims, drops empty description, defaults project_type", () => {
  assert.deepEqual(
    toBoardCreatePayload({ key: " ab ", name: " Alpha ", description: "  ", project_type: "" }),
    { key: "ab", name: "Alpha", project_type: DEFAULT_PROJECT_TYPE },
  );
  assert.deepEqual(
    toBoardCreatePayload({ key: "AB", name: "Alpha", description: "hi", project_type: "unity" }),
    { key: "AB", name: "Alpha", description: "hi", project_type: "unity" },
  );
});

// ---------------------------------------------------------------------------
// mapValidationBody — FastAPI default 422 array envelope
// ---------------------------------------------------------------------------

test("mapValidationBody maps loc→field, unknown loc→form", () => {
  const body = {
    detail: [
      { loc: ["body", "key"], msg: "String should have at most 5 characters" },
      { loc: ["body", "name"], msg: "Field required" },
      { loc: ["body"], msg: "root problem" },
    ],
  };
  const errors = mapValidationBody(body);
  assert.ok(errors);
  assert.match(errors!.key!, /5 characters/);
  assert.match(errors!.name!, /Field required/);
  assert.equal(errors!.form, "root problem");
});

test("mapValidationBody returns null on a non-array detail (string envelope)", () => {
  assert.equal(mapValidationBody({ detail: "nope" }), null);
  assert.equal(mapValidationBody(null), null);
});

// ---------------------------------------------------------------------------
// mapApiErrorToForm — the status-code branch (AC4/AC5/AC6)
// ---------------------------------------------------------------------------

test("mapApiErrorToForm: 409 → key duplicate (no form)", () => {
  const e = mapApiErrorToForm(409, { error: "conflict" }, "Conflict");
  assert.ok(e.key);
  assert.equal(e.form, undefined);
});

test("mapApiErrorToForm: 403 → friendly form permission message", () => {
  const e = mapApiErrorToForm(403, { error: "permission_denied" }, "Permission denied");
  assert.ok(e.form && /yetki/i.test(e.form));
  assert.equal(e.key, undefined);
});

test("mapApiErrorToForm: 422 → field map, falls back to form when unmappable", () => {
  const mapped = mapApiErrorToForm(
    422,
    { detail: [{ loc: ["body", "key"], msg: "too long" }] },
    "Unprocessable",
  );
  assert.match(mapped.key!, /too long/);

  const fallback = mapApiErrorToForm(422, { detail: "not-an-array" }, "Unprocessable");
  assert.equal(fallback.form, "Unprocessable");
});

test("mapApiErrorToForm: other status → raw message at form level", () => {
  const e = mapApiErrorToForm(500, null, "Boom");
  assert.equal(e.form, "Boom");
});

test("BOARD_KEY_MAX sanity", () => {
  assert.equal(BOARD_KEY_MAX, 5);
});
