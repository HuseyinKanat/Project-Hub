/**
 * focusTrap — PH-305
 *
 * The pure Tab / Shift+Tab wrap decision extracted from the modal focus-trap
 * effect that was duplicated verbatim in Lightbox + DocPopup and now lives in the
 * shared `ui/Modal` primitive.
 *
 * Kept deliberately IMPORT-FREE (no `react`, no `@/` alias) so it can be imported
 * via a relative path from `focusTrap.test.ts` under
 * `node --test --experimental-strip-types` — the Vite `@/` alias does not resolve
 * in the Node test runner (repo convention, see grouping.test.ts / R5 in the
 * architect note). The DOM wiring (querySelectorAll → focus) stays in Modal.tsx;
 * only the branch-free decision is unit-tested here.
 */

/**
 * CSS selector for the tabbable elements inside a dialog card. Mirrors the
 * selector the duplicated effect used. `[tabindex="-1"]` is excluded, so the
 * sibling dismiss surface (a `tabIndex={-1}` backdrop button that lives OUTSIDE
 * the card anyway) never enters the trap cycle.
 */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/**
 * Decide where focus should wrap on a Tab / Shift+Tab keypress inside a trapped
 * dialog, given the ordered list of focusable elements and the currently focused
 * one. Returns the element to move focus to (the caller does `preventDefault()`
 * then `.focus()`), or `null` when NO wrap is needed and native Tab movement
 * should proceed:
 *
 *   - forward  (no shift) AND active === last  → wrap to first
 *   - backward (shift)    AND active === first → wrap to last
 *   - middle / active-not-in-list / empty      → null (native handling)
 *
 * A single focusable is its own first AND last, so it pins focus to itself in
 * both directions — matching the original inline behaviour. Generic over `T` so
 * the decision can be unit-tested with plain values (strings) without a DOM.
 *
 * @param focusables ordered tabbable elements (dialog DOM order)
 * @param active     the currently focused element (`document.activeElement`)
 * @param shiftKey   whether Shift was held (backwards tabbing)
 */
export function nextTrapTarget<T>(
  focusables: readonly T[],
  active: T | null,
  shiftKey: boolean,
): T | null {
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (first === undefined || last === undefined) return null; // empty list
  if (shiftKey) {
    return active === first ? last : null;
  }
  return active === last ? first : null;
}
