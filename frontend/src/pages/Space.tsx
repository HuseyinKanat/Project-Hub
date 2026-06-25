/**
 * Space.tsx — PH-277 (epic PH-271, child 6/7): the Obsidian-style /space view.
 * PH-278 (child 7/7, FINAL): tag filter chips driven by the shared `searchFilter`
 * store, wired into the PH-277 `SpaceGraph` controlled seam (highlight, not a
 * server hard-filter).
 *
 * Fetches the cross-board concept graph (`GET /api/graph`, gated by the global
 * `tag.read` cap) and renders it as a force-directed bipartite graph (ticket nodes
 * + tag nodes) via `SpaceGraph`. The `getConceptGraph` call MAY 403 in dev until
 * the live PH board's roles JSON is refreshed (`tag.read` not yet propagated); we
 * surface that honestly in an error branch rather than crashing — the page never
 * white-screens on a permission gap.
 *
 * Filter chips (PH-278): tags selected from the global search box (or by clicking a
 * tag node) land in the `searchFilter` Zustand store and render as `ConceptTagChip`
 * rows above the graph. The MOST-RECENTLY-added tag drives the SpaceGraph highlight
 * via the single-id seam (`selectedTagId="tag:"+id`); `onSelectTag` writes the
 * graph's own tag-node clicks back into the store so chips ⇄ graph stay in sync.
 * The seam is single-tag by design — multi-tag graph narrowing is PH-279 scope.
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";

import { api } from "@/api/client";
import { ConceptTagChip } from "@/components/ConceptTagChip";
import { SpaceGraphPanel } from "@/components/space/SpaceGraphPanel";
import {
  activeSelectedTagId,
  stripTagPrefix,
  useSearchFilter,
} from "@/stores/searchFilter";
import type { ConceptTag, GraphNode } from "@/types/api";

export function SpacePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["concept-graph"],
    // GLOBAL scope — no params ⇒ backend default `scope=global` ⇒ the detailed
    // cross-board topology (byte-identical to PH-274). The board-scoped collapse
    // view lives on the per-board "Space" tab (BoardDetail), NOT here.
    queryFn: () => api.getConceptGraph(),
  });

  const selectedTags = useSearchFilter((s) => s.selectedTags);
  const addTag = useSearchFilter((s) => s.addTag);
  const removeTag = useSearchFilter((s) => s.removeTag);
  const clear = useSearchFilter((s) => s.clear);

  // The single-id the seam wants = the most-recently-added chip, prefixed "tag:".
  const selectedTagId = activeSelectedTagId(selectedTags);

  // SpaceGraph clicks a tag node → write back into the store so the chip appears.
  // A null id (pane click / toggle-off) removes the currently-active chip so the
  // store stays the single source of truth for both directions.
  const onSelectTag = useCallback(
    (prefixed: string | null) => {
      const bareId = stripTagPrefix(prefixed);
      if (!bareId) {
        // graph cleared the highlight → drop the active (last) chip.
        const last = selectedTags.at(-1);
        if (last) removeTag(last.id);
        return;
      }
      // already a chip? nothing to do — it's already the highlight target.
      if (selectedTags.some((t) => t.id === bareId)) return;
      // Build a minimal ConceptTag from the matching graph tag node.
      const node = data?.nodes.find(
        (n: GraphNode) => n.type === "tag" && n.id === prefixed,
      );
      const tag: ConceptTag = {
        id: bareId,
        slug: node?.slug ?? bareId,
        name: node?.label ?? node?.slug ?? bareId,
        color: node?.color ?? null,
      };
      addTag(tag);
    },
    [data, selectedTags, addTag, removeTag],
  );

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Space</h1>
        <p className="text-sm text-text-muted">
          Cross-board concept graph — tickets linked through shared concept tags.
          Click a ticket to open it; click a tag to highlight its neighbourhood.
        </p>
      </header>

      {selectedTags.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-1.5"
          aria-label="Aktif tag filtreleri"
        >
          <span className="text-xs text-text-muted">Filtre:</span>
          {selectedTags.map((tag) => (
            <ConceptTagChip
              key={tag.id}
              tag={tag}
              onRemove={() => removeTag(tag.id)}
            />
          ))}
          <button
            type="button"
            onClick={clear}
            className="btn-ghost text-xs text-text-muted"
          >
            Temizle
          </button>
        </div>
      )}

      <SpaceGraphPanel
        graph={data}
        isLoading={isLoading}
        error={error}
        scope="global"
        selectedTagId={selectedTagId}
        onSelectTag={onSelectTag}
      />
    </section>
  );
}
