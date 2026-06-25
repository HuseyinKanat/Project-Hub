/**
 * LabeledEdge.tsx — PH-277 (epic PH-271, the /space concept graph EXPANSION).
 *
 * A SINGLE custom ReactFlow edge applied to EVERY edge in the graph so each
 * connection's MEANING is legible — the user's core complaint was "bağlantıların
 * ne üzerinden olduğu belli değil" (the reason behind each line is unclear).
 *
 * It renders a low-curvature `BaseEdge` (so the per-type stroke/dash/marker
 * SpaceGraph sets in `style`/`markerEnd` shows through) PLUS a token-styled chip
 * via `EdgeLabelRenderer`, positioned at the path midpoint.
 *
 * PH-280 Feature 1 (edge-click path highlight): the BaseEdge gets an
 * `interactionWidth` (a wider INVISIBLE click halo, ~22px) so the thin line body
 * is reliably clickable for ReactFlow's `onEdgeClick` WITHOUT widening the visible
 * stroke. The chip's `stopPropagation` pins the label independently and must NOT
 * swallow a click on the line body.
 *
 * PH-280 Feature 2 (straighter edges): switched from the full bezier
 * (`getBezierPath`) to `getStraightPath` for low-curvature rendering — combined
 * with the layout-force tuning in SpaceGraph, connected nodes read as near-direct
 * chords. `getStraightPath` returns `[path, labelX, labelY]` (same tuple shape) so
 * the chip midpoint positioning is unaffected.
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
  getStraightPath,
} from "@xyflow/react";

/** Invisible click halo (px) so the thin line body is reliably clickable. */
const EDGE_INTERACTION_WIDTH = 22;

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
  data,
  markerEnd,
  style,
}: EdgeProps) {
  // Feature 2 — straight (low-curvature) path so connected nodes read as direct
  // chords. Same `[path, labelX, labelY]` tuple as the old bezier helper.
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
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
    return (
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={style}
        interactionWidth={EDGE_INTERACTION_WIDTH}
      />
    );
  }

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={style}
        interactionWidth={EDGE_INTERACTION_WIDTH}
      />
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
