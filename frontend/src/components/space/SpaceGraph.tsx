/**
 * SpaceGraph.tsx — PH-277 (epic PH-271, the Obsidian-style /space view).
 *
 * Renders a cross-board concept graph (bipartite: ticket nodes + tag nodes) with
 * a HEADLESS, ONE-SHOT d3-force layout: we build the simulation, tick it to a
 * settled state SYNCHRONOUSLY (no requestAnimationFrame loop), STOP it, then feed
 * the resolved `{x, y}` into ReactFlow's `useNodesState` and `fitView`. ReactFlow
 * owns the canvas/pan/zoom; d3-force is used ONCE for placement only — there is no
 * live physics after mount (cheaper, deterministic, and ReactFlow drag still works
 * because positions live in ReactFlow state thereafter).
 *
 * Interactions:
 *  - Ticket node click → navigate to that ticket's detail route
 *    (`/boards/<board>/tickets/<key>`) — uses node.data.board + node.data.key, no
 *    uuid parsing (AC3).
 *  - Tag node click → FRONTEND-ONLY highlight: dim every non-neighbour, emphasize
 *    the tag + its 1-hop neighbour tickets across ALL boards (AC4). NO backend
 *    call; selection lives in component state.
 *
 * PH-278 SEAM: `selectedTagId` / `onSelectTag` are OPTIONAL controlled props. When
 * omitted, the component owns the selection internally (the PH-277 behaviour). When
 * supplied, selection becomes controlled — PH-278 flips to a parent-driven filter
 * UI WITHOUT touching this component's internals.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  type NodeMouseHandler,
  Panel,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useNavigate } from "react-router-dom";

import { BoardNode, type BoardNodeData } from "@/components/space/BoardNode";
import { LabeledEdge } from "@/components/space/LabeledEdge";
import { TagNode, type TagNodeData } from "@/components/space/TagNode";
import {
  TicketNode,
  type TicketNodeData,
  colorForBoard,
} from "@/components/space/TicketNode";
import type { GraphEdge, GraphNode, GraphResponse } from "@/types/api";

const nodeTypes = { ticket: TicketNode, tag: TagNode, board: BoardNode };
const edgeTypes = { labeled: LabeledEdge };

/**
 * Per-edge-type presentation (PH-277 leg D/E). `label` is the FALLBACK shown when
 * `edge.context` is null — backend (PH-279) supplies `context` for every edge, so
 * this keyed map only guards old/partial responses. The visual fields make the 5
 * connection reasons legible at a glance; SpaceGraph spreads `style` (and layers
 * the highlight opacity over it) per edge.
 */
const EDGE_META: Record<
  GraphEdge["type"],
  {
    /** Full fallback label (when backend `context` is null). */
    label: string;
    /** Compact glyph/word shown by default (user refinement C — expands on click/hover). */
    compact: string;
    stroke: string;
    dash?: string;
    width: number;
    animated: boolean;
    arrow: boolean;
  }
> = {
  has_tag: { label: "has tag", compact: "tag", stroke: "var(--hairline-strong)", width: 1.5, animated: false, arrow: false },
  tag_link: { label: "relates", compact: "rel", stroke: "var(--accent)", width: 1.5, animated: true, arrow: true },
  epic: { label: "epic", compact: "epic", stroke: "var(--text-secondary)", width: 2, animated: false, arrow: true },
  reference: { label: "references", compact: "ref", stroke: "var(--text-muted)", dash: "4 3", width: 1.5, animated: false, arrow: true },
  board: { label: "cross-board", compact: "board", stroke: "var(--accent-strong)", dash: "2 4", width: 2, animated: false, arrow: true },
};

interface SpaceGraphProps {
  graph: GraphResponse;
  /**
   * Graph scope (PH-279). `"global"` (default) = the detailed cross-board /space;
   * `"board"` = the per-board "Space" tab where foreign boards are COLLAPSED into
   * `board`-nodes. Only changes whether a `board`-node click navigates — layout is
   * scope-agnostic (d3-force reads only node ids).
   */
  scope?: "global" | "board";
  /** The owning board KEY in board scope (for context; nav uses node.data.board). */
  boardKey?: string;
  /**
   * PH-278 SEAM — controlled selected tag (prefixed node id, e.g. "tag:<uuid>").
   * Omit ⇒ the component owns selection internally (PH-277 default). `null` ⇒
   * controlled-but-nothing-selected.
   */
  selectedTagId?: string | null;
  /** PH-278 SEAM — controlled selection callback. Omit ⇒ internal state. */
  onSelectTag?: (tagId: string | null) => void;
}

/** d3-force working node — carries the original prefixed id + resolved x/y. */
interface SimNode extends SimulationNodeDatum {
  id: string;
}

/**
 * Spacing control (user refinement A). `spacing` is a UNITLESS multiplier the
 * slider drives LIVE; every distance-bearing force scales off it so the user can
 * spread the graph out or pull it tight without touching code. 1 = the tuned
 * default; the slider exposes ~0.5 … 2.5.
 */
export const DEFAULT_SPACING = 1;
export const MIN_SPACING = 0.5;
export const MAX_SPACING = 2.5;

/** Stable per-board angle on a ring → cluster centroid (user refinement B). */
function clusterKey(node: GraphNode): string {
  // Tickets cluster by their board; tags get their OWN cluster lane (so the
  // taxonomy backbone doesn't smear across board groups); a collapsed board node
  // anchors its own foreign-board lane.
  if (node.type === "tag") return "__tags__";
  if (node.type === "board") return `board:${node.board ?? node.id}`;
  return `board:${node.board ?? "?"}`;
}

/**
 * Run d3-force HEADLESS: build the sim, tick it to settle SYNCHRONOUSLY, stop, and
 * return a `id → {x, y}` map. No RAF loop — a fixed tick count drains the layout in
 * one pass (deterministic + cheap for the modest cross-board node count).
 *
 * CLUSTERING (user refinement B — "clean, non-chaotic"): each board (+ the tag
 * lane) gets a centroid placed on a ring; nodes are pulled toward their cluster
 * centroid via forceX/forceY so boards read as VISUALLY GROUPED blobs instead of
 * one tangled hairball. forceCollide prevents node overlap; charge keeps
 * disconnected components from piling up.
 *
 * SPACING (user refinement A): `spacing` scales link distance, the cluster ring
 * radius, the collide radius and the charge — so the slider spreads/tightens the
 * WHOLE layout live.
 */
function computeLayout(
  graph: GraphResponse,
  spacing: number = DEFAULT_SPACING,
): Map<string, { x: number; y: number }> {
  const simNodes: SimNode[] = graph.nodes.map((n) => ({ id: n.id }));
  const byId = new Map(simNodes.map((n) => [n.id, n]));

  // Build the cluster centroid map: one ring slot per distinct cluster.
  const clusters = Array.from(new Set(graph.nodes.map(clusterKey)));
  const ringRadius = 260 * spacing * Math.max(1, Math.sqrt(clusters.length) / 2);
  const centroid = new Map<string, { x: number; y: number }>();
  clusters.forEach((c, i) => {
    if (clusters.length === 1) {
      centroid.set(c, { x: 0, y: 0 });
      return;
    }
    const angle = (i / clusters.length) * Math.PI * 2;
    centroid.set(c, {
      x: Math.cos(angle) * ringRadius,
      y: Math.sin(angle) * ringRadius,
    });
  });
  // node id → its cluster centroid (precomputed for the forceX/Y accessors).
  const nodeCentroid = new Map<string, { x: number; y: number }>();
  for (const n of graph.nodes) {
    nodeCentroid.set(n.id, centroid.get(clusterKey(n)) ?? { x: 0, y: 0 });
  }

  // Only keep edges whose both endpoints exist (defensive — a tag filter could
  // return an edge to a pruned node).
  const simLinks: SimulationLinkDatum<SimNode>[] = graph.edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));

  const sim = forceSimulation(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((d) => d.id)
        .distance(120 * spacing)
        .strength(0.35),
    )
    // Repulsion scales with spacing so a wider graph also pushes nodes apart more.
    .force("charge", forceManyBody().strength(-320 * spacing))
    // Pull every node toward ITS cluster centroid → grouped, readable blobs.
    .force("clusterX", forceX<SimNode>((d) => nodeCentroid.get(d.id)?.x ?? 0).strength(0.18))
    .force("clusterY", forceY<SimNode>((d) => nodeCentroid.get(d.id)?.y ?? 0).strength(0.18))
    // Prevent node overlap; radius scales with spacing.
    .force("collide", forceCollide(56 * spacing))
    .stop();

  // Drain the simulation in one synchronous pass. 300 ticks settles a graph this
  // size; alphaDecay default (~0.0228) reaches alphaMin well within that budget.
  const ticks = 300;
  for (let i = 0; i < ticks; i += 1) sim.tick();

  const out = new Map<string, { x: number; y: number }>();
  for (const n of simNodes) {
    out.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 });
  }
  return out;
}

/** Build the 1-hop neighbour set (node ids) of `tagId` from the edge list. */
function neighbourIds(graph: GraphResponse, tagId: string): Set<string> {
  const set = new Set<string>([tagId]);
  for (const e of graph.edges) {
    if (e.source === tagId) set.add(e.target);
    if (e.target === tagId) set.add(e.source);
  }
  return set;
}

export function SpaceGraph({
  graph,
  scope = "global",
  selectedTagId: controlledSelectedTagId,
  onSelectTag,
}: Readonly<SpaceGraphProps>) {
  const navigate = useNavigate();
  const { fitView } = useReactFlow();

  // PH-278 SEAM: controlled when `selectedTagId` prop is provided (even as null);
  // otherwise the component owns the selection (PH-277 default).
  const isControlled = controlledSelectedTagId !== undefined;
  const [internalSelectedTagId, setInternalSelectedTagId] = useState<string | null>(
    null,
  );
  const selectedTagId = isControlled
    ? (controlledSelectedTagId ?? null)
    : internalSelectedTagId;

  const setSelectedTagId = useCallback(
    (id: string | null) => {
      if (onSelectTag) onSelectTag(id);
      if (!isControlled) setInternalSelectedTagId(id);
    },
    [onSelectTag, isControlled],
  );

  // User refinement A — LIVE node spacing. Persisted per-scope in localStorage so
  // the user's preferred density survives a reload; defaults to the tuned value.
  const spacingStorageKey = `ph-space-spacing:${scope}`;
  const [spacing, setSpacing] = useState<number>(() => {
    if (typeof window === "undefined") return DEFAULT_SPACING;
    const stored = window.localStorage.getItem(spacingStorageKey);
    const n = stored ? Number.parseFloat(stored) : NaN;
    return Number.isFinite(n) && n >= MIN_SPACING && n <= MAX_SPACING
      ? n
      : DEFAULT_SPACING;
  });
  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(spacingStorageKey, String(spacing));
    }
  }, [spacing, spacingStorageKey]);

  // One-shot layout — recomputed when the graph topology OR the spacing changes
  // (the slider drives a live re-layout + re-fit).
  const layout = useMemo(() => computeLayout(graph, spacing), [graph, spacing]);

  // Base nodes/edges derived from the graph + the settled layout. Highlight flags
  // are layered on top in a separate effect so toggling a selection never re-runs
  // the (more expensive) layout pass.
  const baseNodes = useMemo<Node[]>(
    () =>
      graph.nodes.map((n) => {
        const pos = layout.get(n.id) ?? { x: 0, y: 0 };
        if (n.type === "tag") {
          const data: TagNodeData = {
            type: "tag",
            label: n.label,
            slug: n.slug,
            color: n.color,
          };
          return { id: n.id, type: "tag", position: pos, data };
        }
        if (n.type === "board") {
          // PH-279 collapsed neighbour board (scope=board only).
          const data: BoardNodeData = {
            type: "board",
            label: n.label,
            board: n.board,
            board_id: n.board_id,
          };
          return { id: n.id, type: "board", position: pos, data };
        }
        const data: TicketNodeData = {
          type: "ticket",
          label: n.label,
          board: n.board,
          key: n.key,
          state: n.state,
          title: n.title,
        };
        return { id: n.id, type: "ticket", position: pos, data };
      }),
    [graph, layout],
  );

  // Node id → short label (ticket key / tag name / board key) so an expanded edge
  // label can name its endpoints, e.g. "references PH-274".
  const nodeLabelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of graph.nodes) m.set(n.id, n.key ?? n.label ?? n.id);
    return m;
  }, [graph]);

  const baseEdges = useMemo<Edge[]>(
    () =>
      graph.edges.map((e) => {
        const meta = EDGE_META[e.type] ?? EDGE_META.has_tag;
        // Full label: PREFER the backend-supplied per-edge `context` (it carries
        // the real tag_link relation string); fall back to the type-keyed map only
        // when context is null (old/partial responses) — crash-proof either way.
        const context =
          e.context ??
          (e.type === "tag_link" ? (e.relation ?? meta.label) : meta.label);
        const targetLabel = nodeLabelById.get(e.target) ?? "";
        // user refinement C — a richer EXPANDED string naming the relation + the
        // endpoint it points at (e.g. "references PH-274", "cross-board → KIM").
        const full =
          e.type === "reference"
            ? `references ${targetLabel}`
            : e.type === "board"
              ? `cross-board → ${targetLabel}`
              : e.type === "tag_link"
                ? `relation: ${context}`
                : e.type === "epic"
                  ? "epic parent"
                  : `tagged: ${targetLabel}`;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: "labeled",
          // tag_link stays animated (taxonomy backbone) — folded into EDGE_META.
          animated: meta.animated,
          markerEnd: meta.arrow ? { type: MarkerType.ArrowClosed } : undefined,
          // Per-type stroke / dash / width — the highlight effect later layers
          // ONLY `opacity` over this (spread, never overwrite).
          style: {
            stroke: meta.stroke,
            strokeWidth: meta.width,
            strokeDasharray: meta.dash,
          },
          // `compact` = default tiny label; `full` = expanded on click/hover.
          data: { label: context, compact: meta.compact, full },
        };
      }),
    [graph, nodeLabelById],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(baseNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseEdges);

  // Re-seed ReactFlow state when the graph (and thus layout) changes.
  useEffect(() => {
    setNodes(baseNodes);
  }, [baseNodes, setNodes]);
  useEffect(() => {
    setEdges(baseEdges);
  }, [baseEdges, setEdges]);

  // Fit the viewport once the laid-out nodes are in place.
  useEffect(() => {
    if (baseNodes.length === 0) return;
    // rAF lets ReactFlow measure node DOM before fitting.
    const raf = requestAnimationFrame(() => fitView({ padding: 0.2, duration: 300 }));
    return () => cancelAnimationFrame(raf);
  }, [baseNodes, fitView]);

  // Layer highlight flags (dimmed / emphasized) onto node data + edges whenever the
  // selection changes — pure visual, positions untouched (AC4).
  const highlightSet = useMemo(
    () => (selectedTagId ? neighbourIds(graph, selectedTagId) : null),
    [graph, selectedTagId],
  );

  useEffect(() => {
    setNodes((curr) =>
      curr.map((node) => {
        const inSet = highlightSet?.has(node.id) ?? false;
        return {
          ...node,
          data: {
            ...node.data,
            dimmed: highlightSet ? !inSet : false,
            emphasized: highlightSet ? inSet : false,
          },
        };
      }),
    );
  }, [highlightSet, setNodes]);

  useEffect(() => {
    setEdges((curr) =>
      curr.map((edge) => {
        const active =
          !highlightSet ||
          (highlightSet.has(edge.source) && highlightSet.has(edge.target));
        // MERGE: keep the per-type stroke/dash/width (baseEdges); only layer the
        // highlight opacity on top. Also flag `data.dimmed` so the LabeledEdge
        // chip fades in lockstep with the line.
        return {
          ...edge,
          style: {
            ...edge.style,
            opacity: active ? 1 : 0.12,
            transition: "opacity 150ms",
          },
          data: { ...edge.data, dimmed: !active },
        };
      }),
    );
  }, [highlightSet, setEdges]);

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_evt, node) => {
      if (node.type === "board") {
        // Collapsed neighbour board → navigate to that foreign board (its own
        // space is one click away from there). No deep-link to #space.
        const data = node.data as BoardNodeData;
        if (data.board) navigate(`/boards/${data.board}`);
        return;
      }
      if (node.type === "ticket") {
        const data = node.data as TicketNodeData;
        if (data.board && data.key) {
          navigate(`/boards/${data.board}/tickets/${data.key}`);
        }
        return;
      }
      // Tag node → toggle highlight (clicking the selected tag clears it).
      setSelectedTagId(selectedTagId === node.id ? null : node.id);
    },
    [navigate, selectedTagId, setSelectedTagId],
  );

  // Clicking empty canvas clears any active tag highlight.
  const onPaneClick = useCallback(() => {
    if (selectedTagId) setSelectedTagId(null);
  }, [selectedTagId, setSelectedTagId]);

  // MiniMap node color mirrors the on-canvas accent (board hue / tag hue).
  const miniMapNodeColor = useCallback(
    (node: Node): string => {
      const raw = graph.nodes.find((n) => n.id === node.id);
      if (!raw) return "var(--text-muted)";
      if (raw.type === "tag") return raw.color ?? "var(--text-muted)";
      if (raw.type === "board") return colorForBoard(raw.board);
      return colorForBoard(raw.board);
    },
    [graph],
  );

  // Which edge types are actually present — drives the legend (board scope may
  // not show all 5). Stable across selection (depends only on the graph).
  const presentEdgeTypes = useMemo(() => {
    const order: GraphEdge["type"][] = [
      "has_tag",
      "tag_link",
      "epic",
      "reference",
      "board",
    ];
    const seen = new Set(graph.edges.map((e) => e.type));
    return order.filter((t) => seen.has(t));
  }, [graph]);

  const hasBoardNode = useMemo(
    () => graph.nodes.some((n) => n.type === "board"),
    [graph],
  );

  return (
    <div className="ph-flow h-[calc(100vh-12rem)] min-h-[480px] w-full overflow-hidden rounded-lg border border-hairline bg-inset">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.1}
        proOptions={{ hideAttribution: false }}
        attributionPosition="bottom-left"
      >
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={miniMapNodeColor} />
        <Background gap={20} size={1} />
        {/* Spacing control (user refinement A) — drives a LIVE re-layout: the
            slider scales the d3-force link distance / charge / cluster radius /
            collide radius, recomputes positions, and re-fits the viewport. The
            value persists per-scope in localStorage. */}
        <Panel position="top-right">
          <div
            className="card flex items-center gap-2 px-3 py-2 text-[11px]"
            aria-label="Node spacing"
          >
            <label
              htmlFor="ph-spacing"
              className="font-semibold uppercase tracking-wide text-text-secondary"
            >
              Spacing
            </label>
            <input
              id="ph-spacing"
              type="range"
              min={MIN_SPACING}
              max={MAX_SPACING}
              step={0.05}
              value={spacing}
              onChange={(e) => setSpacing(Number.parseFloat(e.target.value))}
              aria-valuetext={`${spacing.toFixed(2)}×`}
              style={{ accentColor: "var(--accent)" }}
            />
            <span className="mono w-9 text-right text-text-muted">
              {spacing.toFixed(2)}×
            </span>
          </div>
        </Panel>
        {/* Legend — the canonical encoding for the 5 edge types + node shapes
            (PH-277 leg D). Token-styled card; non-blocking chrome. Only lists
            the edge types actually present (board scope may show fewer). */}
        <Panel position="top-left">
          <div
            className="card space-y-2 px-3 py-2 text-[11px]"
            style={{ maxWidth: 200 }}
            aria-label="Graph legend"
          >
            <div className="font-semibold uppercase tracking-wide text-text-secondary">
              Connections
            </div>
            <ul className="space-y-1">
              {presentEdgeTypes.map((t) => {
                const meta = EDGE_META[t];
                return (
                  <li key={t} className="flex items-center gap-2">
                    <span
                      aria-hidden
                      style={{
                        display: "inline-block",
                        width: 18,
                        height: 0,
                        borderTopWidth: meta.width,
                        borderTopStyle: meta.dash ? "dashed" : "solid",
                        borderTopColor: meta.stroke,
                      }}
                    />
                    <span className="text-text-muted">{meta.label}</span>
                  </li>
                );
              })}
            </ul>
            <div className="font-semibold uppercase tracking-wide text-text-secondary">
              Nodes
            </div>
            <ul className="space-y-1 text-text-muted">
              <li className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-sm border-2"
                  style={{ borderColor: "var(--text-secondary)" }}
                />
                ticket
              </li>
              <li className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-full border-2"
                  style={{ borderColor: "var(--accent)" }}
                />
                tag
              </li>
              {(hasBoardNode || scope === "board") && (
                <li className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block h-2.5 w-2.5 rounded border-2 border-dashed"
                    style={{ borderColor: "var(--accent-strong)" }}
                  />
                  board (collapsed)
                </li>
              )}
            </ul>
          </div>
        </Panel>
      </ReactFlow>
    </div>
  );
}
