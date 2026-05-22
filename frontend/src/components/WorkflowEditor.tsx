import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Node,
  Edge,
  addEdge,
  useNodesState,
  useEdgesState,
  Controls,
  MiniMap,
  Background,
  Connection,
  Panel,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Save, Plus, Settings, AlertCircle, Loader2, Lock } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { NodePropertyPanel } from "./NodePropertyPanel";
import { EdgePropertyPanel } from "./EdgePropertyPanel";
import type { WorkflowState, WorkflowTransition } from "@/types/api";

interface WorkflowEditorProps {
  boardKey: string;
  workflowId: string | null;
  states: WorkflowState[];
  transitions: WorkflowTransition[];
  availableRoles: string[];
  readOnly?: boolean;
}

// ---------------------------------------------------------------------------
// Custom WorkflowStateNode — with drag-to-connect Handles
// ---------------------------------------------------------------------------
const WorkflowStateNode = ({ data }: { data: Record<string, unknown> }) => {
  const { label, color, is_initial, is_terminal, onSettingsClick, readOnly } = data;

  return (
    <div
      className="relative rounded-lg border-2 bg-white p-4 shadow-sm dark:bg-slate-800 min-w-[120px]"
      style={{ borderColor: (color as string) || "#94a3b8" }}
    >
      {/* Target handle — left side */}
      <Handle
        type="target"
        position={Position.Left}
        style={{
          background: (color as string) || "#94a3b8",
          border: "2px solid white",
          width: 10,
          height: 10,
          pointerEvents: readOnly ? "none" : "auto",
          opacity: readOnly ? 0.3 : 1,
        }}
      />

      {/* Source handle — right side */}
      <Handle
        type="source"
        position={Position.Right}
        style={{
          background: (color as string) || "#94a3b8",
          border: "2px solid white",
          width: 10,
          height: 10,
          pointerEvents: readOnly ? "none" : "auto",
          opacity: readOnly ? 0.3 : 1,
        }}
        data-handlepos="right"
      />

      {/* State indicator dot — moved to top-right area */}
      <div
        className="absolute -top-2 right-2 h-4 w-4 rounded-full border-2 border-white dark:border-slate-800"
        style={{ backgroundColor: (color as string) || "#94a3b8" }}
      />

      {/* Settings button — moved to top-left to avoid handle collision */}
      <button
        onClick={() => (onSettingsClick as (() => void) | undefined)?.()}
        className="absolute -top-2 -left-2 z-10 rounded-full bg-slate-100 p-1 shadow-sm hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600"
        tabIndex={readOnly ? -1 : 0}
        aria-disabled={readOnly as boolean}
        style={{ pointerEvents: readOnly ? "none" : "auto" }}
      >
        <Settings className="h-3 w-3 text-slate-600 dark:text-slate-300" />
      </button>

      <div className="text-center">
        <div className="font-medium text-slate-900 dark:text-slate-100 text-sm">
          {label as string}
        </div>
        {((is_initial as boolean) || (is_terminal as boolean)) && (
          <div className="mt-1 flex justify-center gap-1">
            {(is_initial as boolean) && (
              <span className="text-[10px] rounded px-1 bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400">
                Initial
              </span>
            )}
            {(is_terminal as boolean) && (
              <span className="text-[10px] rounded px-1 bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400">
                Terminal
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Custom WorkflowTransitionEdge — shows lock icon + field gate label
// ---------------------------------------------------------------------------
function WorkflowTransitionEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const requiredFields: string[] = (data as Record<string, unknown>)?.field_gates
    ? ((data as { field_gates?: { required_fields?: string[] } }).field_gates?.required_fields ?? [])
    : [];

  return (
    <>
      <BaseEdge id={id} path={edgePath} />
      {requiredFields.length > 0 && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: "all",
            }}
            className="rounded bg-white px-1.5 py-0.5 text-[10px] shadow border border-slate-300 dark:bg-slate-800 dark:border-slate-600 flex items-center gap-1"
            data-testid="edge-condition-label"
          >
            <Lock className="h-3 w-3 text-amber-600 shrink-0" />
            <span className="text-slate-700 dark:text-slate-300">
              req: {requiredFields.join(", ")}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { workflowState: WorkflowStateNode };
const edgeTypes = { workflowTransition: WorkflowTransitionEdge };

// Layout nodes in a grid if no positions are stored
const layoutNodes = (states: WorkflowState[]): Node[] => {
  const cols = Math.ceil(Math.sqrt(states.length));

  return states.map((state, index) => {
    const row = Math.floor(index / cols);
    const col = index % cols;

    return {
      id: state.name,
      type: "workflowState",
      position: state.position || {
        x: col * 200 + 100,
        y: row * 150 + 100,
      },
      data: {
        label: state.name,
        color: state.color,
        is_initial: state.is_initial,
        is_terminal: state.is_terminal,
      },
    };
  });
};

export function WorkflowEditor({
  boardKey,
  workflowId,
  states,
  transitions,
  availableRoles,
  readOnly = false,
}: WorkflowEditorProps) {
  const queryClient = useQueryClient();

  // Local state for property panels
  const [selectedNode, setSelectedNode] = useState<WorkflowState | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<WorkflowTransition | null>(null);
  const [isNodePanelOpen, setIsNodePanelOpen] = useState(false);
  const [isEdgePanelOpen, setIsEdgePanelOpen] = useState(false);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  // ReactFlow state
  const initialNodes = useMemo(() => layoutNodes(states), [states]);
  const initialEdges = useMemo(
    () =>
      transitions.map((t) => ({
        id: `${t.from}-${t.to}`,
        source: t.from,
        target: t.to,
        type: "workflowTransition",
        data: { allowed_roles: t.allowed_roles, field_gates: t.field_gates },
      })),
    [transitions],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Track changes for unsaved warning (position/node metadata only — transitions persist immediately)
  useEffect(() => {
    const hasChanges =
      nodes.length !== states.length ||
      nodes.some((node) => {
        const originalState = states.find((s) => s.name === node.id);
        return (
          !originalState ||
          originalState.name !== node.data.label ||
          originalState.color !== node.data.color ||
          originalState.is_initial !== node.data.is_initial ||
          originalState.is_terminal !== node.data.is_terminal
        );
      });

    setHasUnsavedChanges(hasChanges);
  }, [nodes, states]);

  // Sync readOnly into node data for handle pointer-events
  useEffect(() => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          readOnly,
        },
      })),
    );
  }, [readOnly, setNodes]);

  // Set up node settings handlers
  useEffect(() => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          onSettingsClick: readOnly
            ? undefined
            : () => {
                const state = states.find((s) => s.name === node.id);
                if (state) {
                  setSelectedNode(state);
                  setIsNodePanelOpen(true);
                }
              },
        },
      })),
    );
  }, [states, setNodes, readOnly]);

  // ---------------------------------------------------------------------------
  // Mutation: add_transition (immediate persist on drag-to-connect)
  // ---------------------------------------------------------------------------
  const addTransitionMutation = useMutation({
    mutationFn: ({ from, to }: { from: string; to: string }) => {
      if (!workflowId) return Promise.reject(new Error("No workflow selected"));
      return api.addTransition(workflowId, from, to);
    },
    onSuccess: () => {
      setConnectError(null);
      queryClient.invalidateQueries({ queryKey: ["workflows", boardKey] });
    },
    onError: (err: Error, variables) => {
      // Rollback optimistic edge
      const tempId = `${variables.from}-${variables.to}`;
      setEdges((eds) => eds.filter((e) => e.id !== tempId));
      setConnectError(err.message);
    },
  });

  // Handle new connections (drag-to-connect)
  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly) return;
      if (!connection.source || !connection.target) return;

      // Dedup: if edge already exists, silently ignore
      if (edges.some((e) => e.source === connection.source && e.target === connection.target)) {
        return;
      }

      const tempId = `${connection.source}-${connection.target}`;
      const newEdge: Edge = {
        id: tempId,
        source: connection.source,
        target: connection.target,
        type: "workflowTransition",
        data: {},
      };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setEdges((eds) => addEdge(newEdge, eds as any) as any);

      // Persist immediately to backend
      addTransitionMutation.mutate({ from: connection.source, to: connection.target });
    },
    [setEdges, edges, addTransitionMutation, readOnly],
  );

  // Handle edge clicks for editing
  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      const transition: WorkflowTransition = {
        from: edge.source,
        to: edge.target,
        allowed_roles: edge.data?.allowed_roles as string[] | undefined,
        field_gates: edge.data?.field_gates as WorkflowTransition["field_gates"],
      };
      setSelectedEdge(transition);
      setIsEdgePanelOpen(true);
    },
    [],
  );

  // Add new node
  const handleAddNode = () => {
    if (readOnly) return;
    const newName = `State ${nodes.length + 1}`;
    const newNode: Node = {
      id: newName,
      type: "workflowState",
      position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 100 },
      data: {
        label: newName,
        color: "#8b5cf6",
        is_initial: false,
        is_terminal: false,
      },
    };
    setNodes((nds) => [...nds, newNode]);
  };

  // Apply node changes
  const handleNodeApply = (updatedNode: WorkflowState) => {
    setNodes((nds) =>
      nds.map((node) =>
        node.id === selectedNode?.name
          ? {
              ...node,
              id: updatedNode.name,
              data: {
                ...node.data,
                label: updatedNode.name,
                color: updatedNode.color,
                is_initial: updatedNode.is_initial,
                is_terminal: updatedNode.is_terminal,
              },
            }
          : node,
      ),
    );

    // Update edges if node name changed
    if (updatedNode.name !== selectedNode?.name) {
      setEdges((eds) =>
        eds.map((edge) => ({
          ...edge,
          id: edge.id.replace(selectedNode?.name || "", updatedNode.name),
          source: edge.source === selectedNode?.name ? updatedNode.name : edge.source,
          target: edge.target === selectedNode?.name ? updatedNode.name : edge.target,
        })),
      );
    }
  };

  // Apply edge changes (local update; actual MCP persist happens in EdgePropertyPanel)
  const handleEdgeApply = (updatedEdge: WorkflowTransition) => {
    setEdges((eds) =>
      eds.map((edge) =>
        edge.source === updatedEdge.from && edge.target === updatedEdge.to
          ? {
              ...edge,
              data: {
                allowed_roles: updatedEdge.allowed_roles,
                field_gates: updatedEdge.field_gates,
              },
            }
          : edge,
      ),
    );
  };

  // Save mutation: persist state positions + metadata via update_workflow
  const saveWorkflowMutation = useMutation({
    mutationFn: (newStates: WorkflowState[]) => {
      if (!workflowId) return Promise.reject(new Error("No workflow selected"));
      return api.updateWorkflow(workflowId, { states: newStates as unknown[] });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflows", boardKey] });
      queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
    },
  });

  const handleSave = async () => {
    if (readOnly) return;
    try {
      const newStates: WorkflowState[] = nodes.map((node) => ({
        name: node.data.label as string,
        category: (node.data.is_terminal
          ? "done"
          : node.data.is_initial
            ? "new"
            : "active") as "new" | "active" | "done",
        color: node.data.color as string,
        is_initial: node.data.is_initial as boolean,
        is_terminal: node.data.is_terminal as boolean,
        position: node.position,
      }));

      await saveWorkflowMutation.mutateAsync(newStates);
      setHasUnsavedChanges(false);
    } catch (error) {
      console.error("Save failed:", error);
    }
  };

  // ---------------------------------------------------------------------------
  // Mutation: delete_transition (edge Delete key or panel Delete button)
  // ---------------------------------------------------------------------------
  const deleteTransitionMutation = useMutation({
    mutationFn: ({ from, to }: { from: string; to: string }) => {
      if (!workflowId) return Promise.reject(new Error("No workflow selected"));
      return api.deleteTransition(workflowId, from, to);
    },
    onSuccess: () => {
      setConnectError(null);
      queryClient.invalidateQueries({ queryKey: ["workflows", boardKey] });
    },
    onError: (err: Error, variables) => {
      // Rollback: re-add the optimistically removed edge
      setEdges((eds) => [
        ...eds,
        {
          id: `${variables.from}-${variables.to}`,
          source: variables.from,
          target: variables.to,
          type: "workflowTransition",
          data: { allowed_roles: undefined, field_gates: undefined },
        },
      ]);
      setConnectError(err.message);
    },
  });

  // Handle edge deletion via ReactFlow Delete/Backspace key
  const onEdgesDelete = useCallback(
    (edgesToDelete: Edge[]) => {
      if (readOnly) return;
      edgesToDelete.forEach((edge) => {
        deleteTransitionMutation.mutate({ from: edge.source, to: edge.target });
      });
    },
    [deleteTransitionMutation, readOnly],
  );

  // Handle edge deletion via panel Delete button
  const handleEdgeDelete = useCallback(
    (transition: import("@/types/api").WorkflowTransition) => {
      if (readOnly) return;
      // Optimistically remove the edge from local state
      setEdges((eds) =>
        eds.filter((e) => !(e.source === transition.from && e.target === transition.to)),
      );
      setIsEdgePanelOpen(false);
      setSelectedEdge(null);
      deleteTransitionMutation.mutate({ from: transition.from, to: transition.to });
    },
    [deleteTransitionMutation, readOnly, setEdges],
  );

  // Handle node deletion
  const onNodesDelete = useCallback(
    (nodesToDelete: Node[]) => {
      if (readOnly) return;
      const nodeIds = nodesToDelete.map((n) => n.id);
      setEdges((eds) =>
        eds.filter(
          (edge) => !nodeIds.includes(edge.source) && !nodeIds.includes(edge.target),
        ),
      );
    },
    [setEdges, readOnly],
  );

  const isSaving = saveWorkflowMutation.isPending;
  const saveError = saveWorkflowMutation.error;

  // Warn about unsaved changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = "";
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasUnsavedChanges]);

  return (
    <div className="h-[600px] w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onEdgeClick={onEdgeClick}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        attributionPosition="bottom-left"
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable={true}
        deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
      >
        <Panel position="top-right" className="flex gap-2">
          {!readOnly && (
            <>
              <button
                onClick={handleAddNode}
                className="btn-secondary inline-flex items-center text-sm"
                disabled={isSaving}
              >
                <Plus className="mr-2 h-4 w-4" />
                Add State
              </button>
              <button
                onClick={handleSave}
                className="btn-primary inline-flex items-center text-sm"
                disabled={isSaving || !hasUnsavedChanges}
              >
                {isSaving ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                {isSaving ? "Saving..." : "Save Changes"}
              </button>
            </>
          )}
        </Panel>

        {(saveError || connectError) && (
          <Panel position="top-center">
            <div
              className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400"
              role="alert"
            >
              <AlertCircle className="mr-2 inline h-4 w-4" />
              {connectError
                ? `Connection failed: ${connectError}`
                : `Save failed: ${saveError instanceof Error ? saveError.message : "Unknown error"}`}
            </div>
          </Panel>
        )}

        <Controls />
        <MiniMap />
        <Background gap={20} size={1} />
      </ReactFlow>

      {/* Property Panels */}
      <NodePropertyPanel
        isOpen={isNodePanelOpen}
        node={selectedNode}
        onClose={() => {
          setIsNodePanelOpen(false);
          setSelectedNode(null);
        }}
        onApply={handleNodeApply}
        existingNodeNames={nodes.map((n) => n.data.label as string)}
      />

      <EdgePropertyPanel
        isOpen={isEdgePanelOpen}
        edge={selectedEdge}
        onClose={() => {
          setIsEdgePanelOpen(false);
          setSelectedEdge(null);
        }}
        onApply={handleEdgeApply}
        onDelete={handleEdgeDelete}
        availableRoles={availableRoles}
        workflowId={workflowId}
        boardKey={boardKey}
        readOnly={readOnly}
      />
    </div>
  );
}
