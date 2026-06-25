/**
 * Space.tsx — PH-277 (epic PH-271, child 6/7): the Obsidian-style /space view.
 *
 * Fetches the cross-board concept graph (`GET /api/graph`, gated by the global
 * `tag.read` cap) and renders it as a force-directed bipartite graph (ticket nodes
 * + tag nodes) via `SpaceGraph`. The `getConceptGraph` call MAY 403 in dev until
 * the live PH board's roles JSON is refreshed (`tag.read` not yet propagated); we
 * surface that honestly in an error branch rather than crashing — the page never
 * white-screens on a permission gap.
 *
 * `SpaceGraph` uses `useReactFlow()` (for `fitView`), so it MUST be wrapped in a
 * `ReactFlowProvider` here.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { SpaceGraphPanel } from "@/components/space/SpaceGraphPanel";

export function SpacePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["concept-graph"],
    // GLOBAL scope — no params ⇒ backend default `scope=global` ⇒ the detailed
    // cross-board topology (byte-identical to PH-274). The board-scoped collapse
    // view lives on the per-board "Space" tab (BoardDetail), NOT here.
    queryFn: () => api.getConceptGraph(),
  });

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Space</h1>
        <p className="text-sm text-text-muted">
          Cross-board concept graph — tickets linked through shared concept tags.
          Click a ticket to open it; click a tag to highlight its neighbourhood.
        </p>
      </header>

      <SpaceGraphPanel
        graph={data}
        isLoading={isLoading}
        error={error}
        scope="global"
      />
    </section>
  );
}
