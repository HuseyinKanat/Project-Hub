import { create } from "zustand";

/**
 * searchFilter — PH-278 (epic PH-271, child 7/7); PH-280 PIVOT to LABELS. The
 * SHARED channel between the global search box (mounted in `Layout` header, ABOVE
 * the router outlet) and the `/space` filter chips + `SpaceGraph` (mounted inside
 * `SpacePage`, a different subtree). They cannot share via lifted state without
 * wrapping the whole shell in a context provider, so a small Zustand store (the
 * app's existing client-state tool — see `stores/auth.ts`) is the clean conduit.
 *
 * v2 semantics (labels, PH-280):
 *  - `selectedLabels` is the ordered list of active filter chips (most-recently
 *    added LAST). They are plain LABEL STRINGS (e.g. "gamex") — no separate
 *    entity. Label search hits + label-node clicks push here; the chips row +
 *    `Temizle` clear it.
 *  - The SpaceGraph seam is SINGLE-value (`selectedLabel`, the RAW label string),
 *    so the graph highlight is driven by the LAST selected label
 *    (`activeSelectedLabel` below). The component maps the raw value → the node id
 *    `label:<value>` internally; the seam carries the bare value, not a prefix.
 *  - Ephemeral (no persistence): a stale filter across navigations would surprise
 *    the user; they clear it explicitly. Identity-change cache wipes don't touch
 *    Zustand, which is fine — this holds no cross-identity-sensitive data.
 */
interface SearchFilterState {
  selectedLabels: string[];
  /** Add a label (no-op if already present); becomes the active highlight. */
  addLabel: (value: string) => void;
  /** Remove one label by value. */
  removeLabel: (value: string) => void;
  /** Drop all filter chips. */
  clear: () => void;
}

export const useSearchFilter = create<SearchFilterState>((set) => ({
  selectedLabels: [],
  addLabel: (value) =>
    set((s) =>
      s.selectedLabels.includes(value)
        ? s
        : { selectedLabels: [...s.selectedLabels, value] },
    ),
  removeLabel: (value) =>
    set((s) => ({
      selectedLabels: s.selectedLabels.filter((v) => v !== value),
    })),
  clear: () => set({ selectedLabels: [] }),
}));

/**
 * The RAW label value the SpaceGraph seam wants — the MOST-RECENTLY-added label.
 * `null` when no chips are active (controlled-but-nothing-selected). The seam
 * carries the bare value (NOT a prefixed id); SpaceGraph maps it to the node id
 * `label:<value>` internally.
 */
export function activeSelectedLabel(labels: string[]): string | null {
  return labels.at(-1) ?? null;
}
