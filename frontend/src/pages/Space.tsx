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
import { ReactFlowProvider } from "@xyflow/react";

import { api } from "@/api/client";
import { SpaceGraph } from "@/components/space/SpaceGraph";

export function SpacePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["concept-graph"],
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

      {isLoading && (
        <output className="block text-sm text-text-muted" aria-live="polite">
          Yükleniyor…
        </output>
      )}

      {error && (
        <div
          className="rounded-md px-3 py-2 text-sm text-danger"
          style={{ background: "var(--danger-soft)" }}
          role="alert"
          aria-live="polite"
        >
          {error.message}
        </div>
      )}

      {data && data.nodes.length === 0 && (
        <div className="card p-6 text-sm text-text-muted">
          Henüz grafikte gösterilecek concept tag yok. Ticket'lara concept tag
          ekledikçe bağlantılar burada belirir.
        </div>
      )}

      {data && data.nodes.length > 0 && (
        <ReactFlowProvider>
          <SpaceGraph graph={data} />
        </ReactFlowProvider>
      )}
    </section>
  );
}
