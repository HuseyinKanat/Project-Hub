import { useEffect, useState } from "react";

/**
 * useDebouncedValue — PH-278 (epic PH-271). Returns `value` only after it has
 * stayed unchanged for `delayMs` (default 250ms). Used by the global search box
 * so a TanStack Query fires on the SETTLED term, not on every keystroke (the
 * backend `/api/search` round-trip is gated on this debounced value).
 *
 * A fresh `setTimeout` is armed on each change and cleared on the next change /
 * unmount, so only the final pause-after-typing produces the committed value.
 */
export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState<T>(value);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);

  return debounced;
}
