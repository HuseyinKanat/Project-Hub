import type { KeyboardEvent } from "react";

/**
 * Keyboard-activation helpers for elements that carry an `onClick` but are not
 * natively interactive (a `<div>`/`<li>`/`<form>` acting as a button, row, or
 * dismissible backdrop). SonarQube `typescript:S1082`
 * (jsx-a11y/click-events-have-key-events) requires that any visible
 * non-interactive element with a click handler also expose a keyboard listener.
 *
 * These helpers let us add that listener without changing the existing mouse
 * behavior or the rendered markup/styling.
 */

/**
 * Returns an `onKeyDown` handler that invokes `activate` when the user presses
 * Enter or Space, mirroring an `onClick` activation. Space's default scroll is
 * prevented so the activation does not also scroll the page.
 *
 * Use for click-activated rows, list items, and dismissible modal backdrops.
 */
export function onActivateKeyDown<T extends Element>(
  activate: (event: KeyboardEvent<T>) => void,
): (event: KeyboardEvent<T>) => void {
  return (event) => {
    if (event.key === "Enter" || event.key === " ") {
      // Space would otherwise scroll the page; Enter is harmless to default but
      // we keep behavior symmetric.
      if (event.key === " ") event.preventDefault();
      activate(event);
    }
  };
}

/**
 * `onKeyDown` counterpart to `onClick={(e) => e.stopPropagation()}` on an inner
 * dialog surface. Keeps Enter/Space presses inside the dialog from bubbling to a
 * backdrop dismiss handler, matching the click-side `stopPropagation`.
 */
export function stopActivationKeyDown<T extends Element>(
  event: KeyboardEvent<T>,
): void {
  if (event.key === "Enter" || event.key === " ") {
    event.stopPropagation();
  }
}
