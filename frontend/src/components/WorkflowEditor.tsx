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
  NodeChange,
  EdgeChange,
  Panel
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Save, Plus, Settings, AlertCircle, Loader2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { NodePropertyPanel } from "./NodePropertyPanel";
import { EdgePropertyPanel } from "./EdgePropertyPanel";
import type { WorkflowState } from "@/types/api";

interface WorkflowEditorProps {
  boardKey: string;
  states: WorkflowState[];
  transitions: { from: string; to: string; allowed_roles?: string[] }[];
  availableRoles: string[];
}

// Custom node component for workflow states
const WorkflowStateNode = ({ data }: { data: any }) => {
  const { label, color, is_initial, is_terminal, onSettingsClick } = data;

  return (
    <div
      className="relative rounded-lg border-2 bg-white p-4 shadow-sm dark:bg-slate-800 min-w-[120px]"
      style={{ borderColor: color || "#94a3b8" }}
    >
      {/* State indicator */}
      <div
        className="absolute -top-2 left-2 h-4 w-4 rounded-full border-2 border-white dark:border-slate-800"
        style={{ backgroundColor: color || "#94a3b8" }}
      />

      {/* Settings button */}
      <button
        onClick={() => onSettingsClick?.()}
        className="absolute -top-2 -right-2 rounded-full bg-slate-100 p-1 shadow-sm hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600"
      >
        <Settings className="h-3 w-3 text-slate-600 dark:text-slate-300" />
      </button>

      <div className="text-center">
        <div className="font-medium text-slate-900 dark:text-slate-100 text-sm">
          {label}
        </div>
        {(is_initial || is_terminal) && (
          <div className="mt-1 flex justify-center gap-1">
            {is_initial && (
              <span className="text-[10px] rounded px-1 bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400">
                Initial
              </span>
            )}
            {is_terminal && (
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

const nodeTypes = {
  workflowState: WorkflowStateNode
};

// Layout nodes in a grid if no positions are stored
const layoutNodes = (states: WorkflowState[]): Node[] => {
  const cols = Math.ceil(Math.sqrt(states.length));

  return states.map((state, index) => {
    const row = Math.floor(index / cols);
    const col = index % cols;

    return {
      id: state.name,
      type: 'workflowState',
      position: state.position || {
        x: col * 200 + 100,
        y: row * 150 + 100
      },
      data: {
        label: state.name,
        color: state.color,
        is_initial: state.is_initial,
        is_terminal: state.is_terminal
      }
    };
  });
};

export function WorkflowEditor({ boardKey, states, transitions, availableRoles }: WorkflowEditorProps) {
  const queryClient = useQueryClient();

  // Local state for property panels
  const [selectedNode, setSelectedNode] = useState<WorkflowState | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<{ from: string; to: string; allowed_roles?: string[] } | null>(null);
  const [isNodePanelOpen, setIsNodePanelOpen] = useState(false);
  const [isEdgePanelOpen, setIsEdgePanelOpen] = useState(false);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  // ReactFlow state
  const initialNodes = useMemo(() => layoutNodes(states), [states]);
  const initialEdges = useMemo(() =>
    transitions.map(t => ({
      id: `${t.from}-${t.to}`,
      source: t.from,
      target: t.to,
      data: { allowed_roles: t.allowed_roles }
    }))
  , [transitions]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Track changes for unsaved warning
  useEffect(() => {
    const hasChanges =
      nodes.length !== states.length ||
      edges.length !== transitions.length ||
      nodes.some(node => {
        const originalState = states.find(s => s.name === node.id);
        return !originalState ||
          originalState.name !== node.data.label ||
          originalState.color !== node.data.color ||
          originalState.is_initial !== node.data.is_initial ||
          originalState.is_terminal !== node.data.is_terminal;
      }) ||
      edges.some(edge => {
        const originalTransition = transitions.find(t => t.from === edge.source && t.to === edge.target);
        return !originalTransition ||
          JSON.stringify(originalTransition.allowed_roles?.sort()) !== JSON.stringify(edge.data?.allowed_roles?.sort());
      });

    setHasUnsavedChanges(hasChanges);
  }, [nodes, edges, states, transitions]);

  // Set up node settings handlers
  useEffect(() => {
    setNodes(currentNodes =>
      currentNodes.map(node => ({
        ...node,
        data: {
          ...node.data,
          onSettingsClick: () => {
            const state = states.find(s => s.name === node.id);
            if (state) {
              setSelectedNode(state);
              setIsNodePanelOpen(true);
            }
          }
        }
      }))
    );
  }, [states, setNodes]);

  // Handle new connections
  const onConnect = useCallback((connection: Connection) => {
    if (connection.source && connection.target) {
      const newEdge = {
        id: `${connection.source}-${connection.target}`,
        source: connection.source,
        target: connection.target,
        data: { allowed_roles: undefined }
      };
      setEdges(eds => addEdge(newEdge, eds));
    }
  }, [setEdges]);

  // Handle edge clicks for editing
  const onEdgeClick = useCallback((_: any, edge: Edge) => {
    const transition = {
      from: edge.source,
      to: edge.target,
      allowed_roles: edge.data?.allowed_roles as string[] | undefined
    };
    setSelectedEdge(transition);
    setIsEdgePanelOpen(true);
  }, []);

  // Add new node
  const handleAddNode = () => {
    const newName = `State ${nodes.length + 1}`;
    const newNode: Node = {
      id: newName,
      type: 'workflowState',
      position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 100 },
      data: {
        label: newName,
        color: "#8b5cf6",
        is_initial: false,
        is_terminal: false
      }
    };
    setNodes(nds => [...nds, newNode]);
  };

  // Apply node changes
  const handleNodeApply = (updatedNode: WorkflowState) => {
    setNodes(nds => nds.map(node =>
      node.id === selectedNode?.name
        ? {
            ...node,
            id: updatedNode.name,
            data: {
              ...node.data,
              label: updatedNode.name,
              color: updatedNode.color,
              is_initial: updatedNode.is_initial,
              is_terminal: updatedNode.is_terminal
            }
          }
        : node
    ));

    // Update edges if node name changed
    if (updatedNode.name !== selectedNode?.name) {
      setEdges(eds => eds.map(edge => ({
        ...edge,
        id: edge.id.replace(selectedNode?.name || '', updatedNode.name),
        source: edge.source === selectedNode?.name ? updatedNode.name : edge.source,
        target: edge.target === selectedNode?.name ? updatedNode.name : edge.target
      })));
    }
  };

  // Apply edge changes
  const handleEdgeApply = (updatedEdge: { from: string; to: string; allowed_roles?: string[] }) => {
    setEdges(eds => eds.map(edge =>
      edge.source === updatedEdge.from && edge.target === updatedEdge.to
        ? { ...edge, data: { allowed_roles: updatedEdge.allowed_roles } }
        : edge
    ));
  };

  // Save mutations
  const saveStatesMutation = useMutation({
    mutationFn: (newStates: WorkflowState[]) => api.updateWorkflowStates(boardKey, newStates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
    }
  });

  const saveTransitionsMutation = useMutation({
    mutationFn: (newTransitions: { from: string; to: string; allowed_roles?: string[] }[]) =>
      api.updateWorkflowTransitions(boardKey, newTransitions),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
    }
  });

  // Save workflow
  const handleSave = async () => {
    try {
      // Convert nodes to states
      const newStates: WorkflowState[] = nodes.map((node, index) => ({
        name: node.data.label as string,
        category: (node.data.is_terminal ? "done" : node.data.is_initial ? "new" : "active") as "new" | "active" | "done",
        color: node.data.color as string,
        is_initial: node.data.is_initial as boolean,
        is_terminal: node.data.is_terminal as boolean,
        position: node.position
      }));

      // Convert edges to transitions
      const newTransitions = edges.map(edge => ({
        from: edge.source,
        to: edge.target,
        allowed_roles: edge.data?.allowed_roles
      }));

      // Save states first, then transitions
      await saveStatesMutation.mutateAsync(newStates);
      await saveTransitionsMutation.mutateAsync(newTransitions);

      setHasUnsavedChanges(false);
    } catch (error) {
      console.error("Save failed:", error);
    }
  };

  // Handle node deletion
  const onNodesDelete = useCallback((nodesToDelete: Node[]) => {
    const nodeIds = nodesToDelete.map(n => n.id);
    // Remove edges connected to deleted nodes
    setEdges(eds => eds.filter(edge =>
      !nodeIds.includes(edge.source) && !nodeIds.includes(edge.target)
    ));
  }, [setEdges]);

  const isSaving = saveStatesMutation.isPending || saveTransitionsMutation.isPending;
  const saveError = saveStatesMutation.error || saveTransitionsMutation.error;

  // Warn about unsaved changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
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
        nodeTypes={nodeTypes}
        fitView
        attributionPosition="bottom-left"
      >
        <Panel position="top-right" className="flex gap-2">
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
            {isSaving ? 'Saving...' : 'Save Changes'}
          </button>
        </Panel>

        {saveError && (
          <Panel position="top-center">
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400" role="alert">
              <AlertCircle className="mr-2 inline h-4 w-4" />
              Save failed: {saveError instanceof Error ? saveError.message : 'Unknown error'}
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
        existingNodeNames={nodes.map(n => n.data.label as string)}
      />

      <EdgePropertyPanel
        isOpen={isEdgePanelOpen}
        edge={selectedEdge}
        onClose={() => {
          setIsEdgePanelOpen(false);
          setSelectedEdge(null);
        }}
        onApply={handleEdgeApply}
        availableRoles={availableRoles}
      />
    </div>
  );
}