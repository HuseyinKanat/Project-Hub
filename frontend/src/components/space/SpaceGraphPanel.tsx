/**
 * SpaceGraphPanel.tsx — PH-277 (epic PH-271, the /space concept graph EXPANSION).
 *
 * The SHARED loading / error / empty / provider ladder for the concept graph,
 * rendered ONCE so both consumers reuse it WITHOUT duplicating the chrome:
 *  - `pages/Space.tsx` (the global cross-board /space) → `scope="global"`,
 *  - the BoardDetail "Space" TAB (board-scoped collapse view) → `scope="board"`.
 *
 * It wraps `SpaceGraph` in the `ReactFlowProvider` it needs (`useReactFlow().
 * fitView`), and surfaces the `getConceptGraph` error branch (incl. a live 403
 * under the global `ticket.read` gate) gracefully — the panel never white-screens
 * on a permission gap.
 */
import { ReactFlowProvider } from "@xyflow/react";

import { SpaceGraph } from "@/components/space/SpaceGraph";
import type { GraphResponse } from "@/types/api";

interface SpaceGraphPanelProps {
  graph: GraphResponse | undefined;
  isLoading: boolean;
  error: Error | null;
  scope?: "global" | "board";
  boardKey?: string;
  /** Override copy for the empty state (board scope wants a board-specific line). */
  emptyMessage?: string;
  /**
   * PH-280 SEAM pass-through → forwarded VERBATIM to `SpaceGraph` (which owns the
   * highlight logic). Omit ⇒ SpaceGraph stays uncontrolled; supply BOTH to drive
   * the LABEL highlight (raw value) from a parent filter UI. This panel adds NO
   * behaviour — it is a pure conduit so SpaceGraph internals stay untouched.
   */
  selectedLabel?: string | null;
  onSelectLabel?: (value: string | null) => void;
}

export function SpaceGraphPanel({
  graph,
  isLoading,
  error,
  scope = "global",
  boardKey,
  emptyMessage,
  selectedLabel,
  onSelectLabel,
}: Readonly<SpaceGraphPanelProps>) {
  return (
    <>
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

      {graph && graph.nodes.length === 0 && (
        <div className="card p-6 text-sm text-text-muted">
          {emptyMessage ??
            "Henüz grafikte gösterilecek label yok. Ticket'lara label ekledikçe bağlantılar burada belirir."}
        </div>
      )}

      {graph && graph.nodes.length > 0 && (
        <ReactFlowProvider>
          <SpaceGraph
            graph={graph}
            scope={scope}
            boardKey={boardKey}
            selectedLabel={selectedLabel}
            onSelectLabel={onSelectLabel}
          />
        </ReactFlowProvider>
      )}
    </>
  );
}
