/**
 * BranchGraph.tsx — PH-159 (G10)
 *
 * SourceTree-like branch graph built on @xyflow/react.
 * - Fetches git graph via TanStack Query (api.git.getGraph + api.git.getStatus)
 * - Renders CommitNode custom nodes + smoothstep edges
 * - Left-rail BranchLegend with selectedBranch state
 * - Controls + MiniMap + Background for SourceTree ergonomics
 * - Empty / NoRepo / Error / Loading states
 *
 * WS live-sync is handled in BoardDetail.tsx: queryClient.invalidateQueries
 * triggers a silent refetch here; new shas are passed as highlightedShas prop.
 */

import { useCallback, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type NodeMouseHandler,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { GitBranch, AlertCircle, Loader2, RefreshCw } from "lucide-react";

import { api } from "@/api/client";
import { CommitNode } from "./CommitNode";
import { BranchLegend } from "./BranchLegend";
import {
  assignLanes,
  buildNodesAndEdges,
  laneColor,
} from "./branchGraphLayout";

const GRAPH_LIMIT = 200;

// Register custom node type — stable reference outside component
const NODE_TYPES: NodeTypes = { commitNode: CommitNode as unknown as NodeTypes[string] };

interface BranchGraphProps {
  boardKey: string;
  /** Shas that just arrived via WS git_synced — pulse for 3s. Passed from BoardDetail. */
  highlightedShas?: Set<string>;
  /** Fires when a commit node is clicked (G12 detail panel hook point). */
  onCommitSelect?: (sha: string) => void;
  /** Fires when a branch is selected in the legend (G11 detail panel hook point). */
  onBranchSelect?: (branch: string) => void;
}

export function BranchGraph({
  boardKey,
  highlightedShas = new Set<string>(),
  onCommitSelect,
  onBranchSelect,
}: BranchGraphProps) {
  const [selectedBranch, setSelectedBranch] = useState<string | null>(null);
  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------
  const graphQuery = useQuery({
    queryKey: ["git", boardKey, "graph", GRAPH_LIMIT],
    queryFn: () => api.git.getGraph(boardKey, { limit: GRAPH_LIMIT }),
    enabled: Boolean(boardKey),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const statusQuery = useQuery({
    queryKey: ["git", boardKey, "status"],
    queryFn: () => api.git.getStatus(boardKey),
    enabled: Boolean(boardKey),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  // ---------------------------------------------------------------------------
  // Lane assignment + node/edge build (memoized)
  // ---------------------------------------------------------------------------
  const commits = graphQuery.data?.commits ?? [];
  const branches = graphQuery.data?.branches ?? [];

  // Stable memoization key: first sha + last sha + count
  const memoKey = useMemo(() => {
    if (commits.length === 0) return "";
    return `${commits[0]!.sha}_${commits[commits.length - 1]!.sha}_${commits.length}`;
  }, [commits]);

  const laneOfSha = useMemo(
    () => assignLanes(commits, branches),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [memoKey],
  );

  const { nodes, edges } = useMemo(
    () =>
      buildNodesAndEdges(commits, branches, laneOfSha, highlightedShas, boardKey),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [memoKey, highlightedShas, boardKey],
  );

  // ---------------------------------------------------------------------------
  // Event handlers
  // ---------------------------------------------------------------------------
  const handleNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      const sha = node.id;
      setSelectedCommitSha(sha);
      console.log("[BranchGraph] commit selected:", sha);
      onCommitSelect?.(sha);
    },
    [onCommitSelect],
  );

  const handleBranchSelect = useCallback(
    (branch: string) => {
      setSelectedBranch((prev) => (prev === branch ? null : branch));
      onBranchSelect?.(branch);
    },
    [onBranchSelect],
  );

  // ---------------------------------------------------------------------------
  // State rendering
  // ---------------------------------------------------------------------------

  // Loading (both queries in flight)
  if (graphQuery.isLoading || statusQuery.isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        <span className="ml-2 text-sm text-slate-500">Graph yükleniyor…</span>
      </div>
    );
  }

  // No repo connected
  if (statusQuery.data && !statusQuery.data.connected) {
    return (
      <div className="flex h-96 flex-col items-center justify-center gap-3 text-center">
        <GitBranch className="h-10 w-10 text-slate-300 dark:text-slate-600" />
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Bu board&apos;a repo bağlı değil. Settings&apos;ten bağlayabilirsiniz.
          <br />
          <span className="text-xs text-slate-400">(G13'te aktif olacak)</span>
        </p>
        <Link
          to={`/boards/${boardKey}/settings`}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700"
        >
          Settings&apos;e git
        </Link>
      </div>
    );
  }

  // Error state
  if (graphQuery.error) {
    return (
      <div className="flex h-96 flex-col items-center justify-center gap-3">
        <AlertCircle className="h-8 w-8 text-red-400" />
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Graph yüklenirken hata oluştu.
        </p>
        <button
          type="button"
          onClick={() => void graphQuery.refetch()}
          className="flex items-center gap-1 rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Tekrar dene
        </button>
      </div>
    );
  }

  // Empty state — repo connected but no commits
  if (commits.length === 0) {
    return (
      <div className="flex h-96 flex-col items-center justify-center gap-3 text-center">
        <GitBranch className="h-10 w-10 text-slate-300 dark:text-slate-600" />
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Henüz commit yok.
          <br />
          <span className="text-xs text-slate-400">
            Repo&apos;ya commit push edilince burası otomatik güncellenir.
          </span>
        </p>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Ready — full graph
  // ---------------------------------------------------------------------------

  // MiniMap node color by lane
  const miniMapNodeColor = (node: { id: string }) => {
    const found = nodes.find((n) => n.id === node.id);
    return found?.data ? laneColor(found.data.lane as number) : "#94a3b8";
  };

  return (
    <div className="flex gap-3" style={{ height: "calc(100vh - 200px)", minHeight: 480 }}>
      {/* Left rail — branch legend */}
      <BranchLegend
        branches={branches}
        selected={selectedBranch}
        onSelect={handleBranchSelect}
      />

      {/* ReactFlow canvas */}
      <div className="relative flex-1 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
        {selectedCommitSha && (
          <div className="pointer-events-none absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded bg-slate-900/80 px-3 py-1.5 text-xs font-mono text-white backdrop-blur-sm">
            Selected: {selectedCommitSha.slice(0, 12)}
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodeClick={handleNodeClick}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1.5 }}
          minZoom={0.05}
          maxZoom={3}
          nodesDraggable={false}
          nodesConnectable={false}
          deleteKeyCode={null}
          className="bg-slate-50 dark:bg-slate-900"
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={20}
            size={1}
            className="dark:opacity-30"
          />
          <Controls
            className="[&_button]:bg-white [&_button]:dark:bg-slate-800 [&_button]:dark:text-slate-300 [&_button]:dark:border-slate-600"
          />
          <MiniMap
            nodeColor={miniMapNodeColor}
            className="border border-slate-200 dark:border-slate-700"
            maskColor="rgba(0,0,0,0.06)"
          />
        </ReactFlow>
      </div>
    </div>
  );
}
