/**
 * BoardNode.tsx — PH-277 (epic PH-271, the /space concept graph EXPANSION).
 *
 * Custom ReactFlow node (`type: 'board'`) for a COLLAPSED neighbour board — the
 * node the PH-279 graph-v2 `scope=board` mode emits (`id: "board:<KEY>"`) to
 * represent a whole foreign board in ONE aggregate node instead of expanding its
 * tickets/tags. It appears ONLY in the per-board "Space" tab (board scope); the
 * detailed cross-board topology stays on the global /space.
 *
 * Visual language — DELIBERATELY distinct from a ticket (square card) and a tag
 * (rounded pill) so the user instantly reads "this is another board, collapsed":
 *  - a heavier, larger card with a DOUBLE-DASHED outer ring (the "aggregate /
 *    collapsed" signal),
 *  - a leading `Layers` (lucide) board glyph,
 *  - the board name + a small mono board-KEY chip,
 *  - colored by `colorForBoard(data.board)` (the SAME laneColor hue the board
 *    would read in the global view) with a stronger color-mix fill.
 *
 * Click → navigate to that foreign board (`/boards/<key>`), wired in SpaceGraph's
 * onNodeClick. Carries hidden `Handle`s (the `board` edge needs anchor points)
 * and the same `dimmed`/`emphasized` flags as the other nodes for consistency
 * (a board node is never a tag-neighbour, so they rarely fire — but the shape
 * stays uniform so SpaceGraph's highlight mapper needs no special-case).
 */
import { Handle, Position } from "@xyflow/react";
import { Layers } from "lucide-react";

import { colorForBoard } from "@/components/space/TicketNode";

export interface BoardNodeData {
  type: "board";
  label: string;
  board: string | null; // foreign board KEY (e.g. "KIM") — drives nav + color
  board_id: string | null;
  dimmed?: boolean;
  emphasized?: boolean;
  [key: string]: unknown;
}

export function BoardNode({ data }: { data: BoardNodeData }) {
  const color = colorForBoard(data.board);
  const dimmed = data.dimmed === true;
  const emphasized = data.emphasized === true;

  return (
    <div
      className="relative flex items-center gap-2 rounded-xl border-2 border-dashed px-4 py-3 min-w-[140px] transition-opacity"
      style={{
        borderColor: color,
        // Double-dashed ring + stronger fill = the "collapsed aggregate board".
        background: `color-mix(in srgb, ${color} 24%, var(--bg-surface))`,
        boxShadow: emphasized
          ? "var(--glow-cyan)"
          : `0 0 0 3px color-mix(in srgb, ${color} 28%, transparent)`,
        opacity: dimmed ? 0.18 : 1,
      }}
      title={`Board ${data.board ?? data.label} (collapsed)`}
    >
      <Handle
        type="target"
        position={Position.Left}
        isConnectable={false}
        style={{ opacity: 0, width: 6, height: 6 }}
      />
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={false}
        style={{ opacity: 0, width: 6, height: 6 }}
      />

      <Layers aria-hidden className="h-5 w-5 shrink-0" style={{ color }} />
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-semibold text-text-primary">
          {data.label}
        </span>
        {data.board && (
          <span
            className="mono rounded px-1.5 text-[10px] uppercase tracking-wide bg-raised text-text-secondary"
            style={{ alignSelf: "flex-start" }}
          >
            {data.board}
          </span>
        )}
      </div>
    </div>
  );
}
