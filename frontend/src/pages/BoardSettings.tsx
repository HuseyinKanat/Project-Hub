import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { useState, FormEvent } from "react";
import { ArrowLeft, Settings, Workflow, Users, Plus, AlertCircle, X, Lock, GitBranch } from "lucide-react";
import { api } from "@/api/client";
import { WorkflowStateList } from "@/components/WorkflowStateList";
import { WorkflowEditor } from "@/components/WorkflowEditor";
import { WorkflowList } from "@/components/WorkflowList";
import { PermissionMatrix } from "@/components/PermissionMatrix";
import { MembersTab } from "@/components/MembersTab";
import { RepositoryStatusPanel } from "@/components/repository/RepositoryStatusPanel";
import { RepositoryConfigForm } from "@/components/repository/RepositoryConfigForm";
import { RepositoryOperationsPanel } from "@/components/repository/RepositoryOperationsPanel";
import { useBoardRole } from "@/hooks/useMe";
import type { WorkflowResponse, WorkflowState } from "@/types/api";

type TabValue = "general" | "workflow" | "members" | "repository";

export function BoardSettingsPage() {
  const { boardKey = "" } = useParams<{ boardKey: string }>();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabValue>("general");
  const [showAddStateModal, setShowAddStateModal] = useState(false);
  const [newStateName, setNewStateName] = useState("");
  const [newStateColor, setNewStateColor] = useState("#8b5cf6");
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowResponse | null>(null);

  const role = useBoardRole(boardKey);
  const isWorkflowEditor = role === "admin" || role === "pm";
  const isAdmin = role === "admin";

  const boardQuery = useQuery({
    queryKey: ["board", boardKey],
    queryFn: () => api.getBoard(boardKey),
    enabled: Boolean(boardKey),
  });

  const workflowsQuery = useQuery({
    queryKey: ["workflows", boardKey],
    queryFn: async () => {
      const boardId = boardQuery.data?.id;
      if (!boardId) return [] as WorkflowResponse[];
      return api.listWorkflows(boardId);
    },
    enabled: Boolean(boardKey) && Boolean(boardQuery.data?.id) && activeTab === "workflow",
  });

  const ticketsQuery = useQuery({
    queryKey: ["tickets", boardKey],
    queryFn: () => api.listTickets({ board_id: boardKey, limit: 100 }),
    enabled: Boolean(boardKey),
  });

  const gitStatusQuery = useQuery({
    queryKey: ["git", boardKey, "status"],
    queryFn: () => api.git.getStatus(boardKey),
    enabled: Boolean(boardKey) && activeTab === "repository",
    staleTime: 30_000,
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

  const workflows: WorkflowResponse[] = workflowsQuery.data ?? [];
  // selectedWorkflow falls back to first active workflow from list, then board.workflow
  const activeWorkflow = workflows.find((w) => w.is_active) ?? null;
  const editorWorkflow = selectedWorkflow ?? activeWorkflow ?? boardQuery.data?.workflow ?? null;

  const states: WorkflowState[] = editorWorkflow?.states ?? boardQuery.data?.workflow.states ?? [];
  const transitions = editorWorkflow?.transitions ?? boardQuery.data?.workflow.transitions ?? [];

  // Workflows passed to <WorkflowList> — derived without a nested ternary
  // (typescript:S3358): explicit list, else synthesize from the board's
  // embedded workflow, else empty.
  let workflowListItems: WorkflowResponse[];
  if (workflows.length > 0) {
    workflowListItems = workflows;
  } else if (boardQuery.data?.workflow) {
    workflowListItems = [{ ...boardQuery.data.workflow, is_active: true }];
  } else {
    workflowListItems = [];
  }

  const ticketsByState = ticketsQuery.data?.tickets.reduce((acc, t) => {
    acc[t.state] = (acc[t.state] || 0) + 1;
    return acc;
  }, {} as Record<string, number>) ?? {};

  if (boardQuery.isLoading) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="text-center">
          <div className="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-hairline border-t-accent"></div>
          <p className="text-text-secondary">Loading board settings...</p>
        </div>
      </div>
    );
  }

  if (boardQuery.error) {
    return (
      <div className="m-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
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
        <h1 className="text-2xl font-semibold text-text-primary">Board Settings</h1>
        <span className="mono rounded bg-inset px-2 py-1 text-xs text-accent border border-hairline-cyan">
          {boardKey}
        </span>
      </div>

      {/* Tabs */}
      <div className="mb-6 border-b border-hairline">
        <div className="flex gap-1" role="tablist" aria-label="Board settings sections">
          {(["general", "workflow", "members", "repository"] as TabValue[]).map((tab) => {
            const icons = {
              general: <Settings className="h-4 w-4" />,
              workflow: <Workflow className="h-4 w-4" />,
              members: <Users className="h-4 w-4" />,
              repository: <GitBranch className="h-4 w-4" />,
            };
            const labels = { general: "General", workflow: "Workflow", members: "Members", repository: "Repository" };
            const isActive = activeTab === tab;
            return (
              <button
                key={tab}
                role="tab"
                aria-selected={isActive}
                aria-controls={`${tab}-panel`}
                id={`${tab}-tab`}
                onClick={() => setActiveTab(tab)}
                className={`relative inline-flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors duration-fast ease-out ${
                  isActive
                    ? "text-accent"
                    : "text-text-secondary hover:text-text-primary"
                }`}
              >
                {icons[tab]}
                {labels[tab]}
                {isActive && (
                  <span className="pointer-events-none absolute inset-x-2 -bottom-px h-0.5 rounded bg-accent shadow-glow-cyan-sm" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* General Tab */}
      {activeTab === "general" && (
        <div
          id="general-panel"
          role="tabpanel"
          aria-labelledby="general-tab"
          className="card space-y-4 p-6"
        >
          <h2 className="text-lg font-semibold text-text-primary">General Settings</h2>
          <p className="text-sm text-text-secondary">Basic board information and configuration</p>

          <div className="space-y-4 pt-4">
            <label className="block space-y-2">
              <span className="text-sm font-medium text-text-secondary">Board Name</span>
              <input
                className="input"
                defaultValue={boardQuery.data?.name}
                onBlur={(e) => updateBoardMutation.mutate({ name: e.target.value })}
              />
            </label>
            <label className="block space-y-2">
              <span className="text-sm font-medium text-text-secondary">Description</span>
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
        <div
          id="workflow-panel"
          role="tabpanel"
          aria-labelledby="workflow-tab"
          className="space-y-6"
        >
          {/* Read-only banner for non-admin/pm */}
          {!isWorkflowEditor && (
            <div
              className="flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
              role="note"
              data-testid="workflow-readonly-banner"
            >
              <Lock className="h-4 w-4 shrink-0" />
              <span>
                Read-only — admin or pm role required to edit workflows.
              </span>
            </div>
          )}

          {/* Workflow List */}
          <div className="card p-6">
            <h2 className="mb-4 text-lg font-semibold text-text-primary">Workflows</h2>
            {workflowsQuery.isLoading ? (
              <div className="py-4 text-center text-text-muted text-sm">
                Loading workflows...
              </div>
            ) : (
              <WorkflowList
                boardKey={boardKey}
                boardId={boardQuery.data?.id ?? ""}
                workflows={workflowListItems}
                selectedWorkflowId={editorWorkflow?.id ?? null}
                onSelect={(wf) => setSelectedWorkflow(wf)}
                readOnly={!isWorkflowEditor}
                ticketStateUsage={ticketsByState}
              />
            )}
          </div>

          {/* States List */}
          <div className="card p-6">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-text-primary">Workflow States</h2>
                <p className="text-sm text-text-secondary">
                  Drag to reorder states. States define the columns on your kanban board.
                </p>
              </div>
              {isWorkflowEditor && (
                <button
                  onClick={() => setShowAddStateModal(true)}
                  className="btn-primary inline-flex items-center text-sm"
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add State
                </button>
              )}
            </div>

            {boardQuery.isLoading ? (
              <div className="py-8 text-center text-text-muted">Loading states...</div>
            ) : (
              <WorkflowStateList
                states={states}
                ticketCounts={ticketsByState}
                onReorder={handleReorderStates}
                disabled={updateStatesMutation.isPending || !isWorkflowEditor}
                workflowId={editorWorkflow?.id}
                boardKey={boardKey}
                boardId={boardQuery.data?.id}
              />
            )}
          </div>

          {/* Visual Workflow Editor */}
          <div className="card p-6">
            <h2 className="mb-2 text-lg font-semibold text-text-primary">Visual Workflow Editor</h2>
            <p className="mb-4 text-sm text-text-secondary">
              Drag and drop to design your workflow. Click states and transitions to edit properties.
            </p>
            <WorkflowEditor
              boardKey={boardKey}
              boardId={boardQuery.data?.id}
              workflowId={editorWorkflow?.id ?? null}
              states={states}
              transitions={transitions}
              availableRoles={Object.keys(
                (boardQuery.data?.roles as { roles?: Record<string, unknown> } | undefined)?.roles ??
                (boardQuery.data?.roles as Record<string, unknown> | undefined) ??
                {}
              )}
              readOnly={!isWorkflowEditor}
            />
          </div>

          {/* Permissions Matrix — per-transition role grid */}
          <div className="card p-6">
            <h2 className="mb-2 text-lg font-semibold text-text-primary">Permissions Matrix</h2>
            <p className="mb-4 text-sm text-text-secondary">
              Each row is a workflow transition; each column is a board role. Check a cell to allow
              that role to perform the transition. Empty rows (no checkboxes) allow all roles by
              default.
            </p>
            <PermissionMatrix
              workflowId={editorWorkflow?.id ?? null}
              transitions={transitions}
              availableRoles={Object.keys(
                (boardQuery.data?.roles as { roles?: Record<string, unknown> } | undefined)?.roles ??
                (boardQuery.data?.roles as Record<string, unknown> | undefined) ??
                {}
              )}
              boardKey={boardKey}
              boardId={boardQuery.data?.id}
              readOnly={!isWorkflowEditor}
              isLoading={workflowsQuery.isLoading}
            />
          </div>
        </div>
      )}

      {/* Members Tab */}
      {activeTab === "members" && (
        <div
          id="members-panel"
          role="tabpanel"
          aria-labelledby="members-tab"
          className="card p-6"
        >
          <h2 className="mb-2 text-lg font-semibold text-text-primary">Board Members</h2>
          <p className="mb-4 text-sm text-text-secondary">
            Manage which actors have access to this board and their assigned roles.
          </p>
          {boardQuery.data?.id && (
            <MembersTab
              boardId={boardQuery.data.id}
              boardKey={boardKey}
              availableRoles={Object.keys(
                (boardQuery.data?.roles as { roles?: Record<string, unknown> } | undefined)?.roles ??
                (boardQuery.data?.roles as Record<string, unknown> | undefined) ??
                {}
              )}
              boardRoles={
                ((boardQuery.data?.roles as { roles?: Record<string, { permissions: string[] }> } | undefined)?.roles ??
                (boardQuery.data?.roles as Record<string, { permissions: string[] }> | undefined) ??
                {}) as Record<string, { permissions: string[] }>
              }
              isAdmin={role === "admin"}
            />
          )}
        </div>
      )}

      {/* Repository Tab */}
      {activeTab === "repository" && (
        <div
          id="repository-panel"
          role="tabpanel"
          aria-labelledby="repository-tab"
          className="space-y-6"
        >
          {/* Read-only banner for non-admin */}
          {!isAdmin && (
            <div
              className="flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
              role="note"
              data-testid="repository-readonly-banner"
            >
              <Lock className="h-4 w-4 shrink-0" />
              <span>
                Salt okunur — repository yönetimi için admin rolü gerekli.
              </span>
            </div>
          )}

          {/* Status panel */}
          <div className="card p-6">
            <h2 className="mb-4 text-lg font-semibold text-text-primary">
              Bağlantı Durumu
            </h2>
            <RepositoryStatusPanel
              status={gitStatusQuery.data}
              isLoading={gitStatusQuery.isLoading}
              isError={gitStatusQuery.isError}
            />
          </div>

          {/* Config form — admin only */}
          {isAdmin && (
            <div className="card p-6">
              <h2 className="mb-2 text-lg font-semibold text-text-primary">
                {gitStatusQuery.data?.connected ? "Repository Güncelle" : "Repository Bağla"}
              </h2>
              <p className="mb-4 text-sm text-text-secondary">
                {gitStatusQuery.data?.connected
                  ? "Mevcut repository yapılandırmasını güncelleyin."
                  : "Bu board için bir git repository bağlayın."}
              </p>
              <RepositoryConfigForm
                boardKey={boardKey}
                initialValues={
                  gitStatusQuery.data?.repository
                    ? {
                        provider: gitStatusQuery.data.repository.provider,
                        remote_url: gitStatusQuery.data.repository.remote_url,
                        default_branch: gitStatusQuery.data.repository.default_branch,
                        local_path: gitStatusQuery.data.repository.local_path,
                      }
                    : undefined
                }
                onSuccess={() => {
                  queryClient.invalidateQueries({ queryKey: ["git", boardKey, "status"] });
                }}
              />
            </div>
          )}

          {/* Operations panel — admin only, only when connected */}
          {isAdmin && gitStatusQuery.data?.connected && (
            <div className="card p-6">
              <h2 className="mb-4 text-lg font-semibold text-text-primary">
                Operasyonlar
              </h2>
              <RepositoryOperationsPanel
                boardKey={boardKey}
                connected={gitStatusQuery.data.connected}
                localPath={gitStatusQuery.data.repository?.local_path}
              />
            </div>
          )}
        </div>
      )}

      {/* Add State Modal */}
      {showAddStateModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-4 backdrop-blur-sm"
          style={{ background: "var(--bg-overlay)" }}
        >
          {/* Dismiss surface — native button keeps click-outside keyboard-
              operable without a handler on a non-interactive element (S6847/S6848). */}
          <button
            type="button"
            aria-label="Close dialog"
            tabIndex={-1}
            className="absolute inset-0 cursor-default"
            onClick={() => setShowAddStateModal(false)}
          />
          <form
            onSubmit={handleAddState}
            role="dialog"
            aria-modal="true"
            aria-label="Add new state"
            className="card relative z-10 w-full max-w-md space-y-4 p-6"
            style={{
              background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
              borderColor: "var(--hairline-cyan)",
              boxShadow: "var(--shadow-glass)",
            }}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-text-primary">Add New State</h2>
              <button
                type="button"
                onClick={() => setShowAddStateModal(false)}
                className="rounded p-1 hover:bg-raised"
              >
                <X className="h-5 w-5 text-text-muted" />
              </button>
            </div>
            <p className="text-sm text-text-secondary">Create a new workflow state for this board.</p>

            {formError && (
              <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">
                {formError}
              </div>
            )}

            <label className="block space-y-2">
              <span className="text-sm font-medium text-text-secondary">State Name</span>
              <input
                className="input"
                value={newStateName}
                onChange={(e) => setNewStateName(e.target.value)}
                placeholder="e.g., Review"
                required
              />
            </label>

            <label className="block space-y-2">
              <span className="text-sm font-medium text-text-secondary">Color</span>
              <div className="flex items-center gap-3">
                <input
                  type="color"
                  value={newStateColor}
                  onChange={(e) => setNewStateColor(e.target.value)}
                  className="h-10 w-20 cursor-pointer rounded border border-hairline"
                />
                <code className="mono rounded bg-inset px-2 py-1 text-sm text-text-secondary">{newStateColor}</code>
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
