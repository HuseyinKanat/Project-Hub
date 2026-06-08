/**
 * identityGuard.ts — PH-232
 *
 * The single trust-boundary predicate for the auth store. Lives in its own
 * dependency-free leaf module so the security-relevant decision can be unit
 * tested under `node --test --experimental-strip-types` without dragging in
 * zustand, `window`/`localStorage`, or the QueryClient singleton (mirrors the
 * pure-logic-in-its-own-module pattern used by branchGraphLayout.ts).
 */

/**
 * Returns `true` iff the token's actual value CHANGED — i.e. iff this is a real
 * identity transition (login as a different actor, logout, or a 401-auto
 * logout) that MUST drop ALL prior-identity cache via `queryClient.clear()`.
 *
 * Returns `false` for a no-op re-set (`prev === next`): Login's auto-login path
 * re-setting the SAME existing token, or a component re-mount. This guard is
 * what keeps `clear()` from firing on every render → no refetch loop, no
 * flicker (AC: same-token setToken does NOT clear). Both `setToken`
 * (`next = newToken`) and `logout` (`next = null`) route through it, so the
 * semantics live in exactly ONE place.
 */
export const shouldClearCacheOnIdentityChange = (
  prev: string | null,
  next: string | null,
): boolean => prev !== next;
