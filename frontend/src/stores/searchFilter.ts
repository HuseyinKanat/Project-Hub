import { create } from "zustand";

import type { ConceptTag } from "@/types/api";

/**
 * searchFilter — PH-278 (epic PH-271, child 7/7). The SHARED channel between the
 * global search box (mounted in `Layout` header, ABOVE the router outlet) and the
 * `/space` filter chips + `SpaceGraph` (mounted inside `SpacePage`, a different
 * subtree). They cannot share via lifted state without wrapping the whole shell in
 * a context provider, so a small Zustand store (the app's existing client-state
 * tool — see `stores/auth.ts`) is the clean conduit.
 *
 * v1 semantics:
 *  - `selectedTags` is the ordered list of active filter chips (most-recently
 *    added LAST). Tag search hits + tag-node clicks push here; the chips row +
 *    `Temizle` clear it.
 *  - The PH-277 SpaceGraph seam is SINGLE-id (`selectedTagId: "tag:"+id`), so the
 *    graph highlight is driven by the LAST selected tag (`activeTagId` below).
 *    Extra chips stay visible as future-multi-select affordances (PH-279 mode);
 *    in v1 only one tag highlights at a time — by design of the seam.
 *  - Ephemeral (no persistence): a stale filter across navigations would surprise
 *    the user; they clear it explicitly. Identity-change cache wipes don't touch
 *    Zustand, which is fine — this holds no cross-identity-sensitive data.
 */
interface SearchFilterState {
  selectedTags: ConceptTag[];
  /** Add a tag (no-op if already present); becomes the active highlight. */
  addTag: (tag: ConceptTag) => void;
  /** Remove one tag by id. */
  removeTag: (id: string) => void;
  /** Drop all filter chips. */
  clear: () => void;
}

export const useSearchFilter = create<SearchFilterState>((set) => ({
  selectedTags: [],
  addTag: (tag) =>
    set((s) =>
      s.selectedTags.some((t) => t.id === tag.id)
        ? s
        : { selectedTags: [...s.selectedTags, tag] },
    ),
  removeTag: (id) =>
    set((s) => ({ selectedTags: s.selectedTags.filter((t) => t.id !== id) })),
  clear: () => set({ selectedTags: [] }),
}));

/**
 * The node id the PH-277 SpaceGraph seam wants — the MOST-RECENTLY-added tag,
 * prefixed `"tag:"`. `null` when no chips are active (controlled-but-nothing-
 * selected). Strip the prefix when reading `onSelectTag` back (`stripTagPrefix`).
 */
export function activeSelectedTagId(tags: ConceptTag[]): string | null {
  const last = tags.at(-1);
  return last ? `tag:${last.id}` : null;
}

/** Strip the seam's `"tag:"` prefix back to a bare tag UUID (or null). */
export function stripTagPrefix(prefixed: string | null): string | null {
  if (!prefixed) return null;
  return prefixed.startsWith("tag:") ? prefixed.slice("tag:".length) : prefixed;
}
