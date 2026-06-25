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
  MiniMap,
  type Node,
  type NodeMouseHandler,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useNavigate } from "react-router-dom";

import { TagNode, type TagNodeData } from "@/components/space/TagNode";
import { TicketNode, type TicketNodeData } from "@/components/space/TicketNode";
import type { GraphResponse } from "@/types/api";

const nodeTypes = { ticket: TicketNode, tag: TagNode };

interface SpaceGraphProps {
  graph: GraphResponse;
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
 * Run d3-force HEADLESS: build the sim, tick it to settle SYNCHRONOUSLY, stop, and
 * return a `id → {x, y}` map. No RAF loop — a fixed tick count drains the layout in
 * one pass (deterministic + cheap for the modest cross-board node count).
 */
function computeLayout(graph: GraphResponse): Map<string, { x: number; y: number }> {
  const simNodes: SimNode[] = graph.nodes.map((n) => ({ id: n.id }));
  const byId = new Map(simNodes.map((n) => [n.id, n]));

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
        .distance(140)
        .strength(0.4),
    )
    .force("charge", forceManyBody().strength(-380))
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide(60))
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

  // One-shot layout — recomputed only when the graph topology changes.
  const layout = useMemo(() => computeLayout(graph), [graph]);

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

  const baseEdges = useMemo<Edge[]>(
    () =>
      graph.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        // tag_link edges are the cross-tag relations — render them animated so the
        // taxonomy backbone reads distinctly from the has_tag/epic spokes.
        animated: e.type === "tag_link",
      })),
    [graph],
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
        return {
          ...edge,
          style: { opacity: active ? 1 : 0.12, transition: "opacity 150ms" },
        };
      }),
    );
  }, [highlightSet, setEdges]);

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_evt, node) => {
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
      return "var(--text-muted)";
    },
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
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.1}
        proOptions={{ hideAttribution: false }}
        attributionPosition="bottom-left"
      >
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={miniMapNodeColor} />
        <Background gap={20} size={1} />
      </ReactFlow>
    </div>
  );
}
