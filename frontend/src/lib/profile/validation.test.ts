/**
 * validation.test.ts — PH-323
 *
 * Locks the client-side profile validators that back the Profile page's
 * block-before-REST behaviour (E1: an invalid owner_slug disables Save + fires no
 * PUT) and the >255 local_path client cap. Runs via Node's built-in test runner
 * (node:test + native TS strip), NOT the app tsc — the repo convention (see
 * permissions.test.ts, grouping.test.ts). Run:
 *   node --test --experimental-strip-types src/lib/profile/validation.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LOCAL_PATH_MAX,
  OWNER_SLUG_MAX,
  canSaveOwnerSlug,
  validateLocalPath,
  validateOwnerSlug,
} from "./validation.ts";

// ---------------------------------------------------------------------------
// validateOwnerSlug — mirrors backend ^[a-z0-9][a-z0-9-]{0,19}$
// ---------------------------------------------------------------------------

test("owner_slug: valid lowercase/alnum/hyphen slugs pass (null)", () => {
  assert.equal(validateOwnerSlug("huseyin"), null);
  assert.equal(validateOwnerSlug("a"), null);
  assert.equal(validateOwnerSlug("a1-b2-c3"), null);
  assert.equal(validateOwnerSlug("0abc"), null); // leading digit is allowed
});

test("owner_slug: empty is rejected (PUT body requires min_length=1)", () => {
  assert.notEqual(validateOwnerSlug(""), null);
});

test("owner_slug: uppercase / leading hyphen / space / underscore rejected", () => {
  assert.notEqual(validateOwnerSlug("Huseyin"), null); // uppercase
  assert.notEqual(validateOwnerSlug("-abc"), null); // leading hyphen
  assert.notEqual(validateOwnerSlug("a b"), null); // space
  assert.notEqual(validateOwnerSlug("a_b"), null); // underscore
  assert.notEqual(validateOwnerSlug("café"), null); // non-ASCII
});

test("owner_slug: boundary — 20 chars ok, 21 rejected", () => {
  assert.equal(validateOwnerSlug("a".repeat(OWNER_SLUG_MAX)), null);
  assert.notEqual(validateOwnerSlug("a".repeat(OWNER_SLUG_MAX + 1)), null);
});

// ---------------------------------------------------------------------------
// validateLocalPath — empty is the remove sentinel; only >255 blocked
// ---------------------------------------------------------------------------

test("local_path: empty is VALID (remove sentinel — backend deletes the row)", () => {
  assert.equal(validateLocalPath(""), null);
});

test("local_path: a normal opaque path passes verbatim", () => {
  assert.equal(validateLocalPath("/Users/huseyin/Documents/project-hub"), null);
  assert.equal(validateLocalPath("C:\\dev\\project-hub"), null);
});

test("local_path: boundary — 255 ok, 256 rejected", () => {
  assert.equal(validateLocalPath("x".repeat(LOCAL_PATH_MAX)), null);
  assert.notEqual(validateLocalPath("x".repeat(LOCAL_PATH_MAX + 1)), null);
});

// ---------------------------------------------------------------------------
// canSaveOwnerSlug — the E1 enable/disable decision
// ---------------------------------------------------------------------------

test("canSave: human + valid + changed → enabled", () => {
  assert.equal(canSaveOwnerSlug("human", null, "huseyin"), true);
  assert.equal(canSaveOwnerSlug("human", "old-slug", "new-slug"), true);
});

test("canSave: unchanged draft → disabled (no pointless PUT)", () => {
  assert.equal(canSaveOwnerSlug("human", "huseyin", "huseyin"), false);
  assert.equal(canSaveOwnerSlug("human", null, ""), false); // never-set, still empty
});

test("canSave: invalid draft → disabled (E1 — no REST on a regex miss)", () => {
  assert.equal(canSaveOwnerSlug("human", null, "Bad Slug"), false);
  assert.equal(canSaveOwnerSlug("human", "ok", "a".repeat(21)), false);
});

test("canSave: agent → always disabled (owner_slug is human-only, PUT → 403)", () => {
  assert.equal(canSaveOwnerSlug("agent", null, "some-owner"), false);
});
