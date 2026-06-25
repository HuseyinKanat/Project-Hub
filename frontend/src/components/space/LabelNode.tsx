/**
 * LabelNode.tsx — PH-280 (epic PH-271, /space concept graph LABELS pivot).
 *
 * Custom ReactFlow node (`type: 'label'`) for a kanban LABEL (free-text string,
 * e.g. "gamex", "android", "bug"). Visually DISTINCT from a ticket node so the
 * bipartite split reads at a glance: a fully-rounded pill (vs the ticket's square
 * card) with a leading `Tag` glyph and a tinted `color-mix` fill.
 *
 * Labels carry NO stored color (they are plain strings), so the hue is a
 * DETERMINISTIC hash of the label value — `laneColor(hashString(value) % 8)`,
 * exactly the pattern `TicketNode.colorForBoard` uses — so the same label string
 * is always the same color across renders AND across scopes (global ⇄ board tab).
 *
 * Hidden `Handle`s anchor the `has_label` edges (non-editable graph →
 * `connectable={false}`, opacity 0). `data.dimmed` / `data.emphasized` are the
 * same pure-visual highlight flags SpaceGraph derives on label selection (and now
 * on edge-click path highlight).
 */
import { Handle, Position } from "@xyflow/react";
import { Tag } from "lucide-react";

import { laneColor } from "@/components/git/branchGraphLayout";
import { hashString } from "@/lib/utils";

export interface LabelNodeData {
  type: "label";
  label: string;
  dimmed?: boolean;
  emphasized?: boolean;
  [key: string]: unknown;
}

/** Label accent color: a deterministic var(--lane-*) token hashed from the value. */
export function colorForLabel(value: string): string {
  return laneColor(hashString(value) % 8);
}

export function LabelNode({ data }: { data: LabelNodeData }) {
  const color = colorForLabel(data.label);
  const dimmed = data.dimmed === true;
  const emphasized = data.emphasized === true;

  return (
    <div
      className="relative inline-flex items-center gap-1.5 rounded-full border-2 px-3.5 py-1.5 transition-opacity"
      style={{
        borderColor: color,
        background: `color-mix(in srgb, ${color} 16%, var(--bg-surface))`,
        opacity: dimmed ? 0.18 : 1,
        boxShadow: emphasized ? "var(--glow-cyan)" : undefined,
      }}
      title={data.label}
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

      <Tag aria-hidden className="h-3.5 w-3.5 shrink-0" style={{ color }} />
      <span className="text-sm font-medium text-text-primary">{data.label}</span>
    </div>
  );
}
