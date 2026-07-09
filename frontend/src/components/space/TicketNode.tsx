/**
 * TicketNode.tsx — PH-277 (epic PH-271, /space concept graph).
 *
 * Custom ReactFlow node (`type: 'ticket'`) for a ticket in the cross-board graph.
 * Shows the ticket KEY (mono) + state badge; the accent (border + dot) is colored
 * BY BOARD via `laneColor(hashString(board) % 8)` so every board reads a stable,
 * theme-reactive hue and two boards are visually distinct on one canvas (AC2).
 *
 * Mirrors `WorkflowStateNode` (WorkflowEditor.tsx): inline `style` carrying the
 * runtime data-driven color + CSS-var surface tokens (NO hardcoded hex in a
 * className, NO shadcn). Carries hidden `Handle`s — ReactFlow edges still need
 * anchor points, but the graph is non-editable so they are `connectable={false}`
 * and visually suppressed (opacity 0).
 *
 * Highlight: `data.dimmed` (set by SpaceGraph when a label/edge is selected and
 * this ticket is NOT in the highlight set) lowers opacity; `data.emphasized` adds
 * an accent ring. Both are pure visual state derived in SpaceGraph — positions are
 * never touched here.
 *
 * PH-280 Feature 3 (collapsible epics): a ticket node that is itself an EPIC (the
 * source of ≥1 `epic` edge) carries `isEpic`, `collapsed`, and `childCount`. The
 * epic shows a chevron (▾ expanded / ▸ collapsed) and, when collapsed, a `+N`
 * badge naming how many child tickets are hidden. Clicking such a node toggles
 * collapse (SpaceGraph routes the click) instead of navigating.
 */
import { Handle, Position } from "@xyflow/react";

import { laneColor } from "@/components/git/branchGraphLayout";
import { hashString } from "@/lib/utils";

export interface TicketNodeData {
  type: "ticket";
  label: string;
  board: string | null;
  key: string | null;
  state: string | null;
  title: string | null;
  dimmed?: boolean;
  emphasized?: boolean;
  // PH-280 Feature 3 — epic collapse affordance (only set on epic nodes).
  isEpic?: boolean;
  collapsed?: boolean;
  childCount?: number;
  [key: string]: unknown;
}

/** Board → stable lane color (theme-reactive var(--lane-*) token). */
export function colorForBoard(board: string | null): string {
  return laneColor(hashString(board ?? "") % 8);
}

export function TicketNode({ data }: { data: TicketNodeData }) {
  const color = colorForBoard(data.board);
  const dimmed = data.dimmed === true;
  const emphasized = data.emphasized === true;
  const isEpic = data.isEpic === true;
  const collapsed = data.collapsed === true;
  const childCount = data.childCount ?? 0;

  return (
    <div
      className="relative rounded-lg border-2 px-3 py-2 min-w-[96px] transition-opacity"
      style={{
        borderColor: color,
        background: "var(--bg-surface)",
        opacity: dimmed ? 0.18 : 1,
        boxShadow: emphasized ? "var(--glow-cyan)" : undefined,
        // Epic nodes read as collapse toggles — a heavier border hints at it.
        borderWidth: isEpic ? 3 : 2,
      }}
      title={
        isEpic
          ? `${data.title ?? data.label} — ${
              collapsed ? "click to expand" : "click to collapse"
            } (${childCount} child${childCount === 1 ? "" : "ren"})`
          : (data.title ?? data.label)
      }
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

      <div
        className="absolute -top-1.5 -right-1.5 h-3 w-3 rounded-full border-2"
        style={{ backgroundColor: color, borderColor: "var(--bg-base)" }}
        aria-hidden
      />

      {/* Feature 3 — collapsed epic shows a +N hidden-child badge. */}
      {isEpic && collapsed && childCount > 0 && (
        <span
          className="absolute -bottom-2 -right-2 inline-flex items-center rounded-full border px-1.5 text-[10px] font-semibold"
          style={{
            background: "var(--bg-raised)",
            borderColor: color,
            color: "var(--text-primary)",
          }}
          aria-hidden
        >
          +{childCount}
        </span>
      )}

      <div className="flex flex-col items-center gap-1 text-center">
        <span className="mono text-sm font-medium text-text-primary">
          {isEpic && (
            <span aria-hidden className="mr-1 text-text-secondary">
              {collapsed ? "▸" : "▾"}
            </span>
          )}
          {data.key ?? data.label}
        </span>
        {data.state && (
          <span className="rounded px-1.5 text-[10px] uppercase tracking-wide bg-raised text-text-secondary">
            {data.state}
          </span>
        )}
      </div>
    </div>
  );
}
