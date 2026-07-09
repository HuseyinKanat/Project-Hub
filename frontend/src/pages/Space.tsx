/**
 * Space.tsx — PH-277 (epic PH-271): the Obsidian-style /space view. PH-280 PIVOT
 * to LABELS: filter chips are now kanban LABEL strings driven by the shared
 * `searchFilter` store, wired into the `SpaceGraph` controlled seam (highlight,
 * not a server hard-filter).
 *
 * Fetches the cross-board concept graph (`GET /api/graph`, gated by the global
 * `ticket.read` cap, PH-281) and renders it as a force-directed bipartite graph
 * (ticket nodes + LABEL nodes) via `SpaceGraph`. The `getConceptGraph` call MAY
 * 403 if the caller lacks cross-board `ticket.read`; we surface that honestly in
 * an error branch rather than crashing — the page never white-screens.
 *
 * Filter chips (PH-280): labels selected from the global search box (or by clicking
 * a label node) land in the `searchFilter` Zustand store and render as inline label
 * chips above the graph. The MOST-RECENTLY-added label drives the SpaceGraph
 * highlight via the single-value seam (`selectedLabel`); `onSelectLabel` writes the
 * graph's own label-node clicks back into the store so chips ⇄ graph stay in sync.
 */
import { useQuery } from "@tanstack/react-query";
import { Tag, X } from "lucide-react";
import { useCallback } from "react";

import { api } from "@/api/client";
import { colorForLabel } from "@/components/space/LabelNode";
import { SpaceGraphPanel } from "@/components/space/SpaceGraphPanel";
import { activeSelectedLabel, useSearchFilter } from "@/stores/searchFilter";

/** Inline filter chip for an active LABEL (hash-colored, removable). */
function LabelFilterChip({
  value,
  onRemove,
}: Readonly<{ value: string; onRemove: () => void }>) {
  const color = colorForLabel(value);
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-[5px] border px-[7px] py-0.5 text-[11px] font-medium text-text-primary"
      style={{
        borderColor: color,
        borderLeftWidth: "3px",
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
      }}
      title={value}
    >
      <Tag aria-hidden className="h-3 w-3 shrink-0" style={{ color }} />
      <span className="truncate">{value}</span>
      <button
        type="button"
        className="ml-0.5 -mr-0.5 inline-flex shrink-0 items-center rounded-sm text-text-muted transition-colors hover:text-danger"
        aria-label={`${value} filtresini kaldır`}
        onClick={onRemove}
      >
        <X aria-hidden className="h-3 w-3" />
      </button>
    </span>
  );
}

export function SpacePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["concept-graph"],
    // GLOBAL scope — no params ⇒ backend default `scope=global` ⇒ the detailed
    // cross-board topology. The board-scoped collapse view lives on the per-board
    // "Space" tab (BoardDetail), NOT here.
    queryFn: () => api.getConceptGraph(),
  });

  const selectedLabels = useSearchFilter((s) => s.selectedLabels);
  const addLabel = useSearchFilter((s) => s.addLabel);
  const removeLabel = useSearchFilter((s) => s.removeLabel);
  const clear = useSearchFilter((s) => s.clear);

  // The single value the seam wants = the most-recently-added chip (raw label).
  const selectedLabel = activeSelectedLabel(selectedLabels);

  // SpaceGraph clicks a label node → write back into the store so the chip appears.
  // A null value (pane click / ESC / toggle-off) removes the active (last) chip so
  // the store stays the single source of truth for both directions.
  const onSelectLabel = useCallback(
    (value: string | null) => {
      if (!value) {
        const last = selectedLabels.at(-1);
        if (last) removeLabel(last);
        return;
      }
      addLabel(value);
    },
    [selectedLabels, addLabel, removeLabel],
  );

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Space</h1>
        <p className="text-sm text-text-muted">
          Cross-board concept graph — tickets linked through shared labels. Click a
          ticket to open it (an epic to collapse its children); click a label to
          highlight its neighbourhood, or an edge to trace a single connection.
        </p>
      </header>

      {selectedLabels.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-1.5"
          aria-label="Aktif label filtreleri"
        >
          <span className="text-xs text-text-muted">Filtre:</span>
          {selectedLabels.map((value) => (
            <LabelFilterChip
              key={value}
              value={value}
              onRemove={() => removeLabel(value)}
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
        selectedLabel={selectedLabel}
        onSelectLabel={onSelectLabel}
      />
    </section>
  );
}
