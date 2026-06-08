/**
 * identityGuard.test.ts — PH-232
 *
 * Locks the identity-boundary cache-clear guard that fixes "admin gating broken
 * after login: `me` query not invalidated on token change". The defect was that
 * an in-app token switch did NOT invalidate the cached identity, so `isAdmin`
 * stayed stale (false) until a hard reload.
 *
 * The fix has two parts:
 *   1. `useMe()` keys `["me", token]` (token-scoped → a switch auto-refetches the
 *      new identity and can never serve the prior token's `me`). Verified in the
 *      browser (TanStack hook behaviour is not unit-testable in this harness).
 *   2. The Zustand auth store calls `queryClient.clear()` on EVERY real identity
 *      change (login as a different actor, logout, and the two outside-React 401
 *      auto-logouts that route through `logout()`), guarded so a no-op re-set of
 *      the SAME token does NOT clear (→ no refetch loop / flicker).
 *
 * `shouldClearCacheOnIdentityChange(prev, next)` is the single predicate BOTH
 * `setToken` and `logout` route through, so testing it pins the security-relevant
 * semantics: clear iff the token value actually changed. This is the meaningful,
 * harness-testable invariant (the store itself needs window/localStorage/zustand
 * + the QueryClient singleton; the guard is the load-bearing decision).
 *
 * No test-runner dependency is added to package.json (forbidden, PH-190). Runs
 * under Node's built-in test runner with native TS type-stripping:
 *
 *   node --test --experimental-strip-types src/stores/auth.test.ts
 *
 * (Node >= 22.6; the project runs Node 26.)
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldClearCacheOnIdentityChange } from "./identityGuard.ts";

const ADMIN = "change-me-on-first-login"; // global admin token (admin of all boards)
const NON_ADMIN = "some-non-admin-actor-token";

test("AC1/AC3 switch to a DIFFERENT token clears cache (stale identity dropped → isAdmin recomputes)", () => {
  // The bug repro: a non-admin `me` is cached under token A; switching to the
  // admin token B must drop it so isAdmin flips true without a hard reload.
  assert.equal(shouldClearCacheOnIdentityChange(NON_ADMIN, ADMIN), true);
});

test("AC1 login from logged-out (null → token) clears cache", () => {
  assert.equal(shouldClearCacheOnIdentityChange(null, ADMIN), true);
});

test("AC2/AC4 logout (token → null) clears cache (no prior-identity bytes survive)", () => {
  assert.equal(shouldClearCacheOnIdentityChange(ADMIN, null), true);
});

test("AC5 re-setting the SAME token does NOT clear (no refetch loop / flicker)", () => {
  // Login auto-login path re-sets the existing token; component re-mount re-sets
  // the same value. The guard must short-circuit so clear() never fires here.
  assert.equal(shouldClearCacheOnIdentityChange(ADMIN, ADMIN), false);
});

test("AC5 redundant logout while already logged out (null → null) does NOT clear", () => {
  assert.equal(shouldClearCacheOnIdentityChange(null, null), false);
});

test("any two distinct non-null tokens differ → clear (covers actor-to-actor switch)", () => {
  assert.equal(shouldClearCacheOnIdentityChange("actor-a", "actor-b"), true);
});
