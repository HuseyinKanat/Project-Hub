/**
 * SpaceGraph.tsx — PH-277 (Obsidian-style /space view) → PH-280 (labels pivot +
 * 3 UX features), epic PH-271.
 *
 * Renders a cross-board concept graph (bipartite: ticket nodes + LABEL nodes) with
 * a HEADLESS, ONE-SHOT d3-force layout: we build the simulation, tick it to a
 * settled state SYNCHRONOUSLY (no requestAnimationFrame loop), STOP it, then feed
 * the resolved `{x, y}` into ReactFlow's `useNodesState` and `fitView`. ReactFlow
 * owns the canvas/pan/zoom; d3-force is used ONCE for placement only — there is no
 * live physics after mount (cheaper, deterministic, and ReactFlow drag still works
 * because positions live in ReactFlow state thereafter).
 *
 * Interactions:
 *  - Ticket node click → navigate to that ticket's detail route
 *    (`/boards/<board>/tickets/<key>`) — UNLESS the ticket is an EPIC (the source
 *    of ≥1 `epic` edge), in which case the click toggles collapse (Feature 3).
 *  - Label node click → FRONTEND-ONLY highlight: dim every non-neighbour, emphasize
 *    the label + its 1-hop neighbour tickets across ALL boards. NO backend call.
 *  - Edge click (Feature 1) → highlight the edge + its two endpoints, dim the rest.
 *  - ESC / pane click → clears BOTH the label and the edge highlight.
 *
 * PH-280 SEAM: `selectedLabel` / `onSelectLabel` are OPTIONAL controlled props
 * carrying the RAW label value (e.g. "gamex" → highlights node id "label:gamex").
 * Omit ⇒ the component owns selection internally; supply ⇒ a parent filter UI
 * drives the label highlight WITHOUT touching this component's internals.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  type Edge,
  type EdgeMouseHandler,
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
import { LabelNode, type LabelNodeData, colorForLabel } from "@/components/space/LabelNode";
import {
  TicketNode,
  type TicketNodeData,
  colorForBoard,
} from "@/components/space/TicketNode";
import type { GraphEdge, GraphNode, GraphResponse } from "@/types/api";

const nodeTypes = { ticket: TicketNode, label: LabelNode, board: BoardNode };
const edgeTypes = { labeled: LabeledEdge };

/** Strip ONLY the first `label:` prefix from a label node id (value may contain ':'). */
const LABEL_PREFIX = "label:";
function labelValueFromId(id: string): string {
  return id.startsWith(LABEL_PREFIX) ? id.slice(LABEL_PREFIX.length) : id;
}
function labelIdFromValue(value: string): string {
  return `${LABEL_PREFIX}${value}`;
}

/**
 * Per-edge-type presentation. `label` is the FALLBACK shown when `edge.context` is
 * null — backend supplies `context` for every edge, so this keyed map only guards
 * old/partial responses. The visual fields make the 4 connection reasons legible
 * at a glance; SpaceGraph spreads `style` (and layers the highlight opacity over
 * it) per edge.
 */
const EDGE_META: Record<
  GraphEdge["type"],
  {
    /** Full fallback label (when backend `context` is null). */
    label: string;
    /** Compact glyph/word shown by default (expands on click/hover). */
    compact: string;
    stroke: string;
    dash?: string;
    width: number;
    animated: boolean;
    arrow: boolean;
  }
> = {
  has_label: { label: "has label", compact: "label", stroke: "var(--hairline-strong)", width: 1.5, animated: false, arrow: false },
  epic: { label: "epic", compact: "epic", stroke: "var(--text-secondary)", width: 2, animated: false, arrow: true },
  reference: { label: "references", compact: "ref", stroke: "var(--text-muted)", dash: "4 3", width: 1.5, animated: false, arrow: true },
  board: { label: "cross-board", compact: "board", stroke: "var(--accent-strong)", dash: "2 4", width: 2, animated: false, arrow: true },
};

interface SpaceGraphProps {
  graph: GraphResponse;
  /**
   * Graph scope (PH-279). `"global"` (default) = the detailed cross-board /space;
   * `"board"` = the per-board "Space" tab where foreign boards are COLLAPSED into
   * `board`-nodes. Drives whether a `board`-node click navigates AND keys the
   * per-scope sessionStorage for collapsed-epics (Feature 3).
   */
  scope?: "global" | "board";
  /** The owning board KEY in board scope (for context; nav uses node.data.board). */
  boardKey?: string;
  /**
   * PH-280 SEAM — controlled selected label (RAW value, e.g. "gamex" → highlights
   * node id "label:gamex"). Omit ⇒ the component owns selection internally.
   * `null` ⇒ controlled-but-nothing-selected.
   */
  selectedLabel?: string | null;
  /** PH-280 SEAM — controlled selection callback (raw value). Omit ⇒ internal state. */
  onSelectLabel?: (value: string | null) => void;
}

/** d3-force working node — carries the original prefixed id + resolved x/y. */
interface SimNode extends SimulationNodeDatum {
  id: string;
}

/**
 * Feature 3 — derive epics + their children from the EDGE list (no backend flag):
 * an epic is any node that is the `source` of an `epic` edge; its children are the
 * `target`s of those edges. Returns the parent→children map.
 */
function buildEpicChildren(graph: GraphResponse): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (e.type !== "epic") continue;
    const kids = m.get(e.source) ?? [];
    kids.push(e.target);
    m.set(e.source, kids);
  }
  return m;
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
  // Tickets cluster by their board; labels get their OWN cluster lane (so the
  // taxonomy backbone doesn't smear across board groups); a collapsed board node
  // anchors its own foreign-board lane.
  if (node.type === "label") return "__labels__";
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
  epicChildren?: Map<string, string[]>,
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

  // Feature 2 — epic-axis bias: nudge each epic's CHILDREN slightly BELOW the
  // epic so the epic spokes read as near-parallel straight chords instead of a
  // splayed fan. Map child id → a target Y offset relative to the cluster centroid.
  // Strength kept low (≤0.08) so it does not fight the cluster centroids.
  const childYBias = new Map<string, number>();
  if (epicChildren) {
    for (const kids of epicChildren.values()) {
      kids.forEach((childId, i) => {
        // Stagger children downward so the chains read vertically aligned.
        childYBias.set(childId, 80 * spacing * (i + 1));
      });
    }
  }

  // Only keep edges whose both endpoints exist (defensive — a filter/collapse
  // could return an edge to a pruned node).
  const simLinks: SimulationLinkDatum<SimNode>[] = graph.edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));

  const sim = forceSimulation(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((d) => d.id)
        // Feature 2 — tighter link distance + STRONGER alignment so linked pairs
        // sit closer and more in-line (straighter chords).
        .distance(96 * spacing)
        .strength(0.6),
    )
    // Feature 2 — softer repulsion so linked chains bend less; scales with spacing.
    .force("charge", forceManyBody().strength(-240 * spacing))
    // Pull every node toward ITS cluster centroid → grouped, readable blobs.
    .force(
      "clusterX",
      forceX<SimNode>((d) => nodeCentroid.get(d.id)?.x ?? 0).strength(0.18),
    )
    .force(
      "clusterY",
      forceY<SimNode>(
        (d) => (nodeCentroid.get(d.id)?.y ?? 0) + (childYBias.get(d.id) ?? 0),
      ).strength((d) => (childYBias.has(d.id) ? 0.22 : 0.18)),
    )
    // Feature 2 — slightly smaller collide radius (rebalanced for the softer
    // charge) so nodes still don't overlap but pack a touch tighter.
    .force("collide", forceCollide(48 * spacing))
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

/** Build the 1-hop neighbour set (node ids) of `nodeId` from the edge list. */
function neighbourIds(graph: GraphResponse, nodeId: string): Set<string> {
  const set = new Set<string>([nodeId]);
  for (const e of graph.edges) {
    if (e.source === nodeId) set.add(e.target);
    if (e.target === nodeId) set.add(e.source);
  }
  return set;
}

export function SpaceGraph({
  graph,
  scope = "global",
  selectedLabel: controlledSelectedLabel,
  onSelectLabel,
}: Readonly<SpaceGraphProps>) {
  const navigate = useNavigate();
  const { fitView } = useReactFlow();

  // PH-280 SEAM: controlled when `selectedLabel` prop is provided (even as null);
  // otherwise the component owns the selection. The seam carries the RAW label
  // value; internally we work with the node id `label:<value>`.
  const isControlled = controlledSelectedLabel !== undefined;
  const [internalSelectedLabel, setInternalSelectedLabel] = useState<string | null>(
    null,
  );
  const selectedLabelValue = isControlled
    ? (controlledSelectedLabel ?? null)
    : internalSelectedLabel;
  // The label node id the highlight mechanism keys on (null when nothing selected).
  const selectedLabelId = selectedLabelValue
    ? labelIdFromValue(selectedLabelValue)
    : null;

  const setSelectedLabel = useCallback(
    (value: string | null) => {
      if (onSelectLabel) onSelectLabel(value);
      if (!isControlled) setInternalSelectedLabel(value);
    },
    [onSelectLabel, isControlled],
  );

  // Feature 1 — edge-click path highlight. A SECOND selection axis, mutually
  // exclusive with the label highlight (one highlightSet source at a time).
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

  // Feature 3 — collapsed epic ids (per-scope, SESSION-persistent). Derive the
  // epic→children map from the RAW graph (before any collapse filter).
  const epicChildren = useMemo(() => buildEpicChildren(graph), [graph]);
  const epicNodeIds = useMemo(
    () => new Set(epicChildren.keys()),
    [epicChildren],
  );

  const collapsedStorageKey = `ph-space-collapsed:${scope}`;
  const [collapsedEpics, setCollapsedEpics] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.sessionStorage.getItem(collapsedStorageKey);
      const arr = raw ? (JSON.parse(raw) as string[]) : [];
      return new Set(Array.isArray(arr) ? arr : []);
    } catch {
      return new Set();
    }
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.sessionStorage.setItem(
        collapsedStorageKey,
        JSON.stringify(Array.from(collapsedEpics)),
      );
    } catch {
      /* sessionStorage may be unavailable (private mode) — non-fatal. */
    }
  }, [collapsedEpics, collapsedStorageKey]);

  const toggleEpicCollapse = useCallback(
    (epicId: string) => {
      setCollapsedEpics((prev) => {
        const next = new Set(prev);
        if (next.has(epicId)) next.delete(epicId);
        else next.add(epicId);
        return next;
      });
      // Collapsing changes the node set → clear any active highlight for cleanliness.
      setSelectedEdgeId(null);
      setSelectedLabel(null);
    },
    [setSelectedLabel],
  );

  // Feature 3 — the FILTERED graph: hide every child of a collapsed epic + its
  // incident edges, recomputed BEFORE the layout so the canvas reflows around the
  // smaller node set.
  const filteredGraph = useMemo<GraphResponse>(() => {
    if (collapsedEpics.size === 0) return graph;
    const hidden = new Set<string>();
    for (const epicId of collapsedEpics) {
      for (const childId of epicChildren.get(epicId) ?? []) hidden.add(childId);
    }
    if (hidden.size === 0) return graph;
    return {
      nodes: graph.nodes.filter((n) => !hidden.has(n.id)),
      edges: graph.edges.filter(
        (e) => !hidden.has(e.source) && !hidden.has(e.target),
      ),
    };
  }, [graph, collapsedEpics, epicChildren]);

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

  // One-shot layout — recomputed when the FILTERED graph topology (collapse),
  // the spacing, or the epic-child set changes (the slider + collapse both drive a
  // live re-layout + re-fit).
  const layout = useMemo(
    () => computeLayout(filteredGraph, spacing, epicChildren),
    [filteredGraph, spacing, epicChildren],
  );

  // Base nodes/edges derived from the FILTERED graph + the settled layout.
  // Highlight flags are layered on top in a separate effect so toggling a
  // selection never re-runs the (more expensive) layout pass.
  const baseNodes = useMemo<Node[]>(
    () =>
      filteredGraph.nodes.map((n) => {
        const pos = layout.get(n.id) ?? { x: 0, y: 0 };
        if (n.type === "label") {
          const data: LabelNodeData = {
            type: "label",
            label: n.label,
          };
          return { id: n.id, type: "label", position: pos, data };
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
        // Ticket node — Feature 3: flag epics (source of ≥1 epic edge) with their
        // collapse state + hidden-child count so TicketNode renders the affordance.
        const isEpic = epicNodeIds.has(n.id);
        const data: TicketNodeData = {
          type: "ticket",
          label: n.label,
          board: n.board,
          key: n.key,
          state: n.state,
          title: n.title,
          ...(isEpic
            ? {
                isEpic: true,
                collapsed: collapsedEpics.has(n.id),
                childCount: epicChildren.get(n.id)?.length ?? 0,
              }
            : {}),
        };
        return { id: n.id, type: "ticket", position: pos, data };
      }),
    [filteredGraph, layout, epicNodeIds, collapsedEpics, epicChildren],
  );

  // Node id → short label (ticket key / label value / board key) so an expanded
  // edge label can name its endpoints, e.g. "references PH-274".
  const nodeLabelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of filteredGraph.nodes) m.set(n.id, n.key ?? n.label ?? n.id);
    return m;
  }, [filteredGraph]);

  const baseEdges = useMemo<Edge[]>(
    () =>
      filteredGraph.edges.map((e) => {
        const meta = EDGE_META[e.type] ?? EDGE_META.has_label;
        // Full label: PREFER the backend-supplied per-edge `context`; fall back to
        // the type-keyed map only when context is null (old/partial responses).
        const context = e.context ?? meta.label;
        const targetLabel = nodeLabelById.get(e.target) ?? "";
        // A richer EXPANDED string naming the relation + the endpoint it points at
        // (e.g. "references PH-274", "cross-board → KIM", "labeled gamex").
        const full =
          e.type === "reference"
            ? `references ${targetLabel}`
            : e.type === "board"
              ? `cross-board → ${targetLabel}`
              : e.type === "epic"
                ? "epic parent"
                : `labeled ${targetLabel}`;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: "labeled",
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
    [filteredGraph, nodeLabelById],
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
  // selection changes — pure visual, positions untouched. TWO mutually-exclusive
  // highlight sources (one at a time):
  //  - a selected LABEL → its 1-hop neighbour set, OR
  //  - a selected EDGE (Feature 1) → its two endpoints.
  const highlightSet = useMemo(() => {
    if (selectedEdgeId) {
      const edge = filteredGraph.edges.find((e) => e.id === selectedEdgeId);
      return edge ? new Set([edge.source, edge.target]) : null;
    }
    if (selectedLabelId) return neighbourIds(filteredGraph, selectedLabelId);
    return null;
  }, [filteredGraph, selectedLabelId, selectedEdgeId]);

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
        // Feature 1 — when an EDGE is selected, ONLY that edge is active (its two
        // endpoints are in highlightSet, but other edges between them are dimmed).
        // Otherwise (label highlight) an edge is active iff BOTH endpoints are in
        // the set.
        const active = selectedEdgeId
          ? edge.id === selectedEdgeId
          : !highlightSet ||
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
  }, [highlightSet, selectedEdgeId, setEdges]);

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
        // Feature 3 — an EPIC ticket node toggles collapse instead of navigating;
        // a leaf ticket navigates as before.
        if (epicNodeIds.has(node.id)) {
          toggleEpicCollapse(node.id);
          return;
        }
        const data = node.data as TicketNodeData;
        if (data.board && data.key) {
          navigate(`/boards/${data.board}/tickets/${data.key}`);
        }
        return;
      }
      // Label node → toggle highlight (clicking the selected label clears it).
      // Clicking a label also clears any active edge highlight (mutually excl.).
      const value = labelValueFromId(node.id);
      setSelectedEdgeId(null);
      setSelectedLabel(selectedLabelValue === value ? null : value);
    },
    [
      navigate,
      epicNodeIds,
      toggleEpicCollapse,
      selectedLabelValue,
      setSelectedLabel,
    ],
  );

  // Feature 1 — clicking an EDGE highlights its path (the edge + its 2 endpoints)
  // and dims the rest. Mutually exclusive with the label highlight.
  const onEdgeClick = useCallback<EdgeMouseHandler>(
    (_evt, edge) => {
      setSelectedLabel(null);
      setSelectedEdgeId((curr) => (curr === edge.id ? null : edge.id));
    },
    [setSelectedLabel],
  );

  // Clicking empty canvas clears BOTH highlights.
  const clearHighlights = useCallback(() => {
    if (selectedLabelValue) setSelectedLabel(null);
    if (selectedEdgeId) setSelectedEdgeId(null);
  }, [selectedLabelValue, selectedEdgeId, setSelectedLabel]);

  const onPaneClick = useCallback(() => {
    clearHighlights();
  }, [clearHighlights]);

  // Feature 1 — ESC clears both highlights (AC explicitly requires ESC).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") clearHighlights();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [clearHighlights]);

  // MiniMap node color mirrors the on-canvas accent (board hue / label hue).
  const miniMapNodeColor = useCallback(
    (node: Node): string => {
      const raw = filteredGraph.nodes.find((n) => n.id === node.id);
      if (!raw) return "var(--text-muted)";
      if (raw.type === "label") return colorForLabel(raw.label);
      return colorForBoard(raw.board);
    },
    [filteredGraph],
  );

  // Which edge types are actually present — drives the legend (board scope may
  // not show all 4). Stable across selection (depends only on the filtered graph).
  const presentEdgeTypes = useMemo(() => {
    const order: GraphEdge["type"][] = [
      "has_label",
      "epic",
      "reference",
      "board",
    ];
    const seen = new Set(filteredGraph.edges.map((e) => e.type));
    return order.filter((t) => seen.has(t));
  }, [filteredGraph]);

  const hasBoardNode = useMemo(
    () => filteredGraph.nodes.some((n) => n.type === "board"),
    [filteredGraph],
  );

  return (
    <div className="ph-flow h-[calc(100vh-12rem)] min-h-[480px] w-full overflow-hidden rounded-lg border border-hairline bg-inset">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
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
                label
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
