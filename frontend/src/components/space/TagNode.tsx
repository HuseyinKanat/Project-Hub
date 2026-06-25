/**
 * TagNode.tsx — PH-277 (epic PH-271, /space concept graph).
 *
 * Custom ReactFlow node (`type: 'tag'`) for a concept tag. Visually DISTINCT from
 * a ticket node so the bipartite split reads at a glance (AC1): a fully-rounded
 * pill (vs the ticket's square card) with a leading `Tag` glyph and a tinted
 * `color-mix` fill — exactly the ConceptTagChip (PH-276) visual language.
 *
 * Color resolves from `data.color` (the tag's authored hue) or, when null, a
 * deterministic `var(--lane-*)` token hashed from the slug — same `resolveTagColor`
 * rule as ConceptTagChip, so a tag reads the same hue in the chip and the graph.
 * Inline `style` carries the runtime data value (NOT a hardcoded hex, NOT shadcn).
 *
 * Hidden `Handle`s anchor the `has_tag` / `tag_link` edges (non-editable graph →
 * `connectable={false}`, opacity 0). `data.dimmed` / `data.emphasized` are the
 * same pure-visual highlight flags SpaceGraph derives on tag selection.
 */
import { Handle, Position } from "@xyflow/react";
import { Tag } from "lucide-react";

import { laneColor } from "@/components/git/branchGraphLayout";
import { hashString } from "@/lib/utils";

export interface TagNodeData {
  type: "tag";
  label: string;
  slug: string | null;
  color: string | null;
  dimmed?: boolean;
  emphasized?: boolean;
  [key: string]: unknown;
}

/** Tag accent color: authored `color`, else a deterministic var(--lane-*) by slug. */
export function colorForTag(data: { color: string | null; slug: string | null }): string {
  const authored = data.color?.trim();
  if (authored) return authored;
  return laneColor(hashString(data.slug ?? "") % 8);
}

export function TagNode({ data }: { data: TagNodeData }) {
  const color = colorForTag(data);
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
      title={data.slug ?? data.label}
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
