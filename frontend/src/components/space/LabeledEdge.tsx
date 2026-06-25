/**
 * LabeledEdge.tsx — PH-277 (epic PH-271, the /space concept graph EXPANSION).
 *
 * A SINGLE custom ReactFlow edge applied to EVERY edge in the graph so each
 * connection's MEANING is legible — the user's core complaint was "bağlantıların
 * ne üzerinden olduğu belli değil" (the reason behind each line is unclear).
 *
 * It renders the standard bezier `BaseEdge` (so the per-type stroke/dash/marker
 * SpaceGraph sets in `style`/`markerEnd` shows through) PLUS a token-styled chip
 * via `EdgeLabelRenderer`, positioned at the bezier midpoint.
 *
 * USER REFINEMENT C — the label is COMPACT by default (a tiny type glyph, e.g.
 * "ref" / "tag" / "rel" / "epic" / "board") and EXPANDS to the full context on
 * HOVER or CLICK (e.g. "references PH-274", "cross-board → KIM", "relation:
 * depends-on"). Click toggles a sticky-expanded state (collapse on second click /
 * blur); hover expands transiently. Only the chip itself is interactive
 * (`pointerEvents:'all'` on the chip, `'none'` on the wrapper) so it NEVER blocks
 * node clicks / pane drag elsewhere on the canvas (AC: "etiketler tıklanabilirliği/
 * navigasyonu bozmaz").
 *
 * Styled with CSS-var tokens (handwritten Tailwind house style, NO shadcn). When
 * the edge is dimmed by the tag-highlight (SpaceGraph layers `style.opacity`), the
 * chip fades with it via `data.dimmed`.
 */
import { useState } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  type EdgeProps,
  getBezierPath,
} from "@xyflow/react";

export interface LabeledEdgeData {
  /** Full context string (backend `edge.context` w/ type fallback). */
  label: string;
  /** Tiny default glyph/word shown until hover/click expands it. */
  compact?: string;
  /** Rich expanded string naming the relation + endpoint. */
  full?: string;
  /** Dimmed by the active tag-highlight set (mirrors SpaceGraph edge opacity). */
  dimmed?: boolean;
  [key: string]: unknown;
}

export function LabeledEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
  style,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const d = (data ?? {}) as LabeledEdgeData;
  const dimmed = d.dimmed === true;

  // Expansion (user refinement C): hovered = transient, pinned = sticky (click).
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const expanded = hovered || pinned;

  // Compact by default, full when expanded. Always have *something* to show.
  const compactText = d.compact ?? d.label;
  const fullText = d.full ?? d.label;
  const text = expanded ? fullText : compactText;

  if (!text) {
    return <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} />;
  }

  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        {/* Wrapper is pointer-transparent; only the chip captures pointer events
            so node clicks / pane drag elsewhere are never intercepted. */}
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: "none",
          }}
        >
          <button
            type="button"
            className="ph-edge-label nodrag nopan"
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            onClick={(e) => {
              e.stopPropagation();
              setPinned((p) => !p);
            }}
            onBlur={() => setPinned(false)}
            aria-expanded={expanded}
            aria-label={`${fullText}${pinned ? "" : " — click to pin"}`}
            title={fullText}
            style={{
              pointerEvents: "all",
              cursor: "pointer",
              fontSize: 10,
              lineHeight: 1.3,
              padding: expanded ? "2px 8px" : "1px 6px",
              borderRadius: "var(--radius-pill)",
              background: expanded ? "var(--bg-surface)" : "var(--bg-raised)",
              border: `1px solid ${pinned ? "var(--accent)" : "var(--hairline)"}`,
              color: expanded ? "var(--text-primary)" : "var(--text-muted)",
              whiteSpace: "nowrap",
              boxShadow: pinned ? "var(--glow-cyan-sm)" : undefined,
              opacity: dimmed ? 0.12 : 1,
              transition: "opacity 150ms, padding 120ms, background 120ms",
            }}
          >
            {text}
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
