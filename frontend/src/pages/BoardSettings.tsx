import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { useState, FormEvent } from "react";
import { ArrowLeft, Settings, Workflow, Users, Shield, Plus, AlertCircle, X } from "lucide-react";
import { api } from "@/api/client";
import { WorkflowStateList } from "@/components/WorkflowStateList";
import { WorkflowTransitionGraph } from "@/components/WorkflowTransitionGraph";
import type { WorkflowState } from "@/types/api";

type TabValue = "general" | "workflow" | "members";

export function BoardSettingsPage() {
  const { boardKey = "" } = useParams<{ boardKey: string }>();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabValue>("general");
  const [showAddStateModal, setShowAddStateModal] = useState(false);
  const [newStateName, setNewStateName] = useState("");
  const [newStateColor, setNewStateColor] = useState("#8b5cf6");
  const [formError, setFormError] = useState<string | null>(null);

  const boardQuery = useQuery({
    queryKey: ["board", boardKey],
    queryFn: () => api.getBoard(boardKey),
    enabled: Boolean(boardKey),
  });

  const ticketsQuery = useQuery({
    queryKey: ["tickets", boardKey],
    queryFn: () => api.listTickets({ board_id: boardKey, limit: 100 }),
    enabled: Boolean(boardKey),
  });

  const updateBoardMutation = useMutation({
    mutationFn: (payload: { name?: string; description?: string }) =>
      api.updateBoard(boardKey, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
    },
  });

  const addStateMutation = useMutation({
    mutationFn: (state: { name: string; color: string }) =>
      api.addWorkflowState(boardKey, { ...state, is_initial: false, is_terminal: false }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
      setShowAddStateModal(false);
      setNewStateName("");
      setNewStateColor("#8b5cf6");
      setFormError(null);
    },
    onError: (err) => {
      setFormError(err instanceof Error ? err.message : "Failed to add state");
    },
  });

  const updateStatesMutation = useMutation({
    mutationFn: (states: WorkflowState[]) =>
      api.updateWorkflowStates(boardKey, states.map((s, i) => ({ ...s, order: i }))),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
    },
  });

  const handleAddState = (e: FormEvent) => {
    e.preventDefault();
    if (!newStateName.trim()) {
      setFormError("State name is required");
      return;
    }
    const states = boardQuery.data?.workflow.states ?? [];
    if (states.some(s => s.name.toLowerCase() === newStateName.trim().toLowerCase())) {
      setFormError("A state with this name already exists");
      return;
    }
    addStateMutation.mutate({ name: newStateName.trim(), color: newStateColor });
  };

  const handleReorderStates = (newOrder: WorkflowState[]) => {
    updateStatesMutation.mutate(newOrder);
  };

  const states: WorkflowState[] = boardQuery.data?.workflow.states ?? [];
  const transitions = boardQuery.data?.workflow.transitions ?? [];

  const ticketsByState = ticketsQuery.data?.tickets.reduce((acc, t) => {
    acc[t.state] = (acc[t.state] || 0) + 1;
    return acc;
  }, {} as Record<string, number>) ?? {};

  if (boardQuery.isLoading) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="text-center">
          <div className="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-slate-300 border-t-slate-600 dark:border-slate-600 dark:border-t-slate-300"></div>
          <p className="text-slate-600 dark:text-slate-400">Loading board settings...</p>
        </div>
      </div>
    );
  }

  if (boardQuery.error) {
    return (
      <div className="m-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
        <AlertCircle className="mr-2 inline h-4 w-4" />
        Failed to load board settings. Please try again.
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-5xl p-6">
      <div className="mb-6 flex items-center gap-4">
        <Link to={`/boards/${boardKey}`} className="btn-ghost inline-flex items-center text-sm">
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Board
        </Link>
        <h1 className="text-2xl font-semibold dark:text-slate-100">Board Settings</h1>
        <span className="rounded bg-slate-900 px-2 py-1 font-mono text-xs text-white dark:bg-slate-700">
          {boardKey}
        </span>
      </div>

      {/* Tabs */}
      <div className="mb-6 border-b border-slate-200 dark:border-slate-700">
        <div className="flex gap-1">
          {(["general", "workflow", "members"] as TabValue[]).map((tab) => {
            const icons = { general: <Settings className="h-4 w-4" />, workflow: <Workflow className="h-4 w-4" />, members: <Users className="h-4 w-4" /> };
            const labels = { general: "General", workflow: "Workflow", members: "Members" };
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`inline-flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === tab
                    ? "border-indigo-500 text-slate-900 dark:border-indigo-400 dark:text-slate-100"
                    : "border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                }`}
              >
                {icons[tab]}
                {labels[tab]}
              </button>
            );
          })}
        </div>
      </div>

      {/* General Tab */}
      {activeTab === "general" && (
        <div className="card space-y-4 p-6">
          <h2 className="text-lg font-semibold dark:text-slate-100">General Settings</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400">Basic board information and configuration</p>
          
          <div className="space-y-4 pt-4">
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Board Name</span>
              <input
                className="input"
                defaultValue={boardQuery.data?.name}
                onBlur={(e) => updateBoardMutation.mutate({ name: e.target.value })}
              />
            </label>
            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Description</span>
              <input
                className="input"
                defaultValue={boardQuery.data?.description ?? ""}
                onBlur={(e) => updateBoardMutation.mutate({ description: e.target.value })}
              />
            </label>
          </div>
        </div>
      )}

      {/* Workflow Tab */}
      {activeTab === "workflow" && (
        <div className="space-y-6">
          {/* States List */}
          <div className="card p-6">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold dark:text-slate-100">Workflow States</h2>
                <p className="text-sm text-slate-600 dark:text-slate-400">
                  Drag to reorder states. States define the columns on your kanban board.
                </p>
              </div>
              <button
                onClick={() => setShowAddStateModal(true)}
                className="btn-primary inline-flex items-center text-sm"
              >
                <Plus className="mr-2 h-4 w-4" />
                Add State
              </button>
            </div>

            {boardQuery.isLoading ? (
              <div className="py-8 text-center text-slate-500 dark:text-slate-400">Loading states...</div>
            ) : (
              <WorkflowStateList
                states={states}
                ticketCounts={ticketsByState}
                onReorder={handleReorderStates}
                disabled={updateStatesMutation.isPending}
              />
            )}
          </div>

          {/* Transition Graph */}
          <div className="card p-6">
            <h2 className="mb-2 text-lg font-semibold dark:text-slate-100">Transition Graph</h2>
            <p className="mb-4 text-sm text-slate-600 dark:text-slate-400">
              Visual representation of allowed state transitions
            </p>
            <WorkflowTransitionGraph states={states} transitions={transitions} />
          </div>
        </div>
      )}

      {/* Members Tab */}
      {activeTab === "members" && (
        <div className="card p-6">
          <h2 className="mb-2 text-lg font-semibold dark:text-slate-100">Board Members</h2>
          <p className="mb-4 text-sm text-slate-600 dark:text-slate-400">Manage board access and roles (coming soon)</p>
          <div className="flex items-center justify-center py-8 text-slate-500 dark:text-slate-400">
            <Shield className="mr-2 h-5 w-5" />
            Member management is not yet implemented
          </div>
        </div>
      )}

      {/* Add State Modal */}
      {showAddStateModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 px-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          onClick={() => setShowAddStateModal(false)}
        >
          <form
            onSubmit={handleAddState}
            onClick={(e) => e.stopPropagation()}
            className="card w-full max-w-md space-y-4 p-6"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold dark:text-slate-100">Add New State</h2>
              <button
                type="button"
                onClick={() => setShowAddStateModal(false)}
                className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-700"
              >
                <X className="h-5 w-5 text-slate-500 dark:text-slate-400" />
              </button>
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-400">Create a new workflow state for this board.</p>

            {formError && (
              <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400" role="alert">
                {formError}
              </div>
            )}

            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700 dark:text-slate-300">State Name</span>
              <input
                className="input"
                value={newStateName}
                onChange={(e) => setNewStateName(e.target.value)}
                placeholder="e.g., Review"
                required
              />
            </label>

            <label className="block space-y-2">
              <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Color</span>
              <div className="flex items-center gap-3">
                <input
                  type="color"
                  value={newStateColor}
                  onChange={(e) => setNewStateColor(e.target.value)}
                  className="h-10 w-20 cursor-pointer rounded border dark:border-slate-600"
                />
                <code className="rounded bg-slate-100 px-2 py-1 text-sm dark:bg-slate-700 dark:text-slate-300">{newStateColor}</code>
              </div>
            </label>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={() => setShowAddStateModal(false)}
                disabled={addStateMutation.isPending}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary text-sm"
                disabled={addStateMutation.isPending || !newStateName.trim()}
              >
                {addStateMutation.isPending ? "Adding..." : "Add State"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
