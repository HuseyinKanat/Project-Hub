import { useState } from "react";
import { Plus, CheckCircle2, Circle, Zap, Info, Trash2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiRequestError } from "@/api/client";
import type { WorkflowResponse } from "@/types/api";

interface WorkflowListProps {
  boardKey: string;
  boardId: string;
  workflows: WorkflowResponse[];
  selectedWorkflowId: string | null;
  onSelect: (workflow: WorkflowResponse) => void;
  readOnly?: boolean;
  /** States currently used by in-flight tickets (for orphan warning) */
  ticketStateUsage?: Record<string, number>;
}

export function WorkflowList({
  boardKey,
  boardId,
  workflows,
  selectedWorkflowId,
  onSelect,
  readOnly = false,
  ticketStateUsage = {},
}: WorkflowListProps) {
  const qc = useQueryClient();
  const [activateTarget, setActivateTarget] = useState<WorkflowResponse | null>(null);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkflowResponse | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: async () => {
      // Prefer active workflow as template; fall back to any workflow; last resort: empty.
      const template = workflows.find((w) => w.is_active) ?? workflows[0];
      const templateStates = template?.states ?? [];
      return api.createWorkflow({
        name: `Workflow ${workflows.length + 1}`,
        states: templateStates as unknown[],
        transitions: [],
        is_default: false,
        // Pass board_id so the backend inserts a BoardWorkflow junction row (is_active=False).
        // list_workflows(board_id) will return it; user can activate via the Activate button.
        board_id: boardId,
      });
    },
    onSuccess: (newWorkflow) => {
      qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
      onSelect(newWorkflow);
    },
  });

  const activateMutation = useMutation({
    mutationFn: (workflowId: string) =>
      api.activateWorkflow(boardId, workflowId),
    onSuccess: () => {
      setActivateTarget(null);
      setActivateError(null);
      qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
    },
    onError: (err: Error) => {
      setActivateError(err.message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (workflowId: string) =>
      api.deleteWorkflow(workflowId, boardId),
    onSuccess: (_data, workflowId) => {
      setDeleteTarget(null);
      setDeleteError(null);
      qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
      // Fallback selection if deleted workflow was selected
      if (selectedWorkflowId === workflowId) {
        const remaining = workflows.filter((w) => w.id !== workflowId);
        if (remaining.length > 0 && remaining[0]) {
          onSelect(remaining[0]);
        }
      }
    },
    onError: (err: Error) => {
      const reason =
        err instanceof ApiRequestError && err.body && "reason" in err.body
          ? (err.body as Record<string, string>).reason
          : null;
      const reasonMessages: Record<string, string> = {
        active_workflow_cannot_delete:
          "Aktif workflow silinemez; önce başka bir workflow'u aktifleştirin.",
        last_workflow: "En az bir workflow bulunmalıdır.",
        default_workflow_protected: "Varsayılan workflow silinemez.",
        workflow_is_board_legacy_fk:
          "Bu workflow bir board tarafından referans ediliyor; önce o board'un aktif workflow'unu değiştirin.",
      };
      setDeleteError(
        reason && reason in reasonMessages
          ? (reasonMessages[reason] ?? err.message)
          : err.message,
      );
    },
  });

  /** Compute states in ticketStateUsage that the target workflow does NOT have */
  function orphanedStates(target: WorkflowResponse): string[] {
    const targetStateNames = new Set(target.states.map((s) => s.name));
    return Object.entries(ticketStateUsage)
      .filter(([state, count]) => count > 0 && !targetStateNames.has(state))
      .map(([state]) => state);
  }

  const orphaned = activateTarget ? orphanedStates(activateTarget) : [];
  const orphanCount = orphaned.reduce(
    (sum, s) => sum + (ticketStateUsage[s] ?? 0),
    0,
  );

  return (
    <div className="space-y-3" data-testid="workflow-list">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-secondary">
          Workflows
        </h3>
        {!readOnly && (
          <button
            type="button"
            data-testid="new-workflow-btn"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="btn-secondary inline-flex items-center text-xs"
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            {createMutation.isPending ? "Creating..." : "+ New workflow"}
          </button>
        )}
      </div>

      {workflows.length === 0 ? (
        <p className="text-xs text-text-muted py-2">
          No workflows found. Create one to get started.
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline">
          {workflows.map((wf) => (
            <li
              key={wf.id}
              className={`flex items-center justify-between px-3 py-2.5 cursor-pointer transition-colors hover:bg-raised ${
                selectedWorkflowId === wf.id
                  ? "bg-accent-soft"
                  : ""
              }`}
              onClick={() => onSelect(wf)}
              data-testid={`workflow-row-${wf.id}`}
            >
              <div className="flex items-center gap-2 min-w-0">
                {wf.is_active ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-success shrink-0" />
                ) : (
                  <Circle className="h-3.5 w-3.5 text-text-muted shrink-0" />
                )}
                <span className="truncate text-sm text-text-primary">
                  {wf.name}
                </span>
                {wf.is_active && (
                  <span className="shrink-0 rounded-full bg-success-soft px-1.5 py-0.5 text-[10px] font-medium text-success">
                    active
                  </span>
                )}
              </div>

              {!readOnly && (
                <div className="ml-2 shrink-0 flex items-center gap-1">
                  {!wf.is_active && (
                    <button
                      type="button"
                      data-testid={`activate-btn-${wf.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setActivateTarget(wf);
                        setActivateError(null);
                      }}
                      disabled={activateMutation.isPending}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-accent ring-1 ring-hairline-cyan hover:bg-accent-soft"
                    >
                      <Zap className="h-3 w-3" />
                      Activate
                    </button>
                  )}
                  {(() => {
                    const canDelete =
                      !wf.is_active &&
                      !wf.is_default &&
                      workflows.length > 1;
                    const deleteTooltip = workflows.length === 1
                      ? "En az bir workflow bulunmalıdır."
                      : wf.is_active
                        ? "Aktif workflow silinemez; önce başka bir workflow'u aktifleştirin."
                        : wf.is_default
                          ? "Varsayılan workflow silinemez."
                          : "Workflow'u sil";
                    return (
                      <button
                        type="button"
                        data-testid={`delete-btn-${wf.id}`}
                        title={deleteTooltip}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (canDelete) {
                            setDeleteTarget(wf);
                            setDeleteError(null);
                          }
                        }}
                        disabled={!canDelete || deleteMutation.isPending}
                        className="inline-flex items-center rounded p-1 text-text-muted hover:text-danger hover:bg-danger-soft disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    );
                  })()}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Delete confirmation dialog */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm px-4"
          style={{ background: "var(--bg-overlay)" }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-dialog-title"
          onClick={() => {
            if (!deleteMutation.isPending) setDeleteTarget(null);
          }}
        >
          <div
            className="card w-full max-w-sm space-y-4 p-5"
            style={{
              background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
              borderColor: "var(--hairline-cyan)",
              boxShadow: "var(--shadow-glass)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2
              id="delete-dialog-title"
              className="text-base font-semibold text-text-primary"
            >
              &quot;{deleteTarget.name}&quot; silinsin mi?
            </h2>

            <p className="text-sm text-text-secondary">
              Bu işlem geri alınamaz. Workflow&apos;a ait state&apos;lerdeki
              ticket&apos;lar etkilenmeyecek, kanban&apos;da &quot;Unknown&quot;
              column&apos;unda görünmeye devam edecek.
            </p>

            {deleteError && (
              <p
                className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger"
                role="alert"
              >
                {deleteError}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={() => setDeleteTarget(null)}
                disabled={deleteMutation.isPending}
              >
                İptal
              </button>
              <button
                type="button"
                data-testid="confirm-delete-btn"
                className="btn text-sm text-text-on-accent"
                style={{ backgroundColor: "var(--danger)" }}
                onClick={() => deleteMutation.mutate(deleteTarget.id)}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? "Siliniyor..." : "Sil"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Activate confirmation dialog */}
      {activateTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm px-4"
          style={{ background: "var(--bg-overlay)" }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="activate-dialog-title"
          onClick={() => setActivateTarget(null)}
        >
          <div
            className="card w-full max-w-sm space-y-4 p-5"
            style={{
              background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
              borderColor: "var(--hairline-cyan)",
              boxShadow: "var(--shadow-glass)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2
              id="activate-dialog-title"
              className="text-base font-semibold text-text-primary"
            >
              Activate &quot;{activateTarget.name}&quot;?
            </h2>

            {orphaned.length > 0 && (
              <div
                data-testid="activate-info-orphan"
                className="rounded-md bg-info-soft p-3"
              >
                <div className="flex items-start gap-2 text-info">
                  <Info className="mt-0.5 h-4 w-4 shrink-0" />
                  <div className="text-xs">
                    <p className="font-medium">
                      This workflow does not include {orphaned.length} state
                      {orphaned.length > 1 ? "s" : ""} currently used by{" "}
                      {orphanCount} ticket{orphanCount > 1 ? "s" : ""}:
                    </p>
                    <ul className="mt-1 list-disc pl-4">
                      {orphaned.map((s) => (
                        <li key={s}>
                          {s} ({ticketStateUsage[s]} ticket
                          {ticketStateUsage[s] !== 1 ? "s" : ""})
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 opacity-80">
                      These tickets will remain visible with their current state
                      until you either add matching states to the new workflow
                      or move the tickets manually.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {!orphaned.length && (
              <p
                data-testid="activate-info-no-orphan"
                className="text-sm text-text-secondary"
              >
                The currently active workflow will be deactivated. This change
                takes effect immediately.
              </p>
            )}

            {activateError && (
              <p
                className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger"
                role="alert"
              >
                {activateError}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={() => setActivateTarget(null)}
                disabled={activateMutation.isPending}
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="confirm-activate-btn"
                className="btn-primary text-sm"
                onClick={() => activateMutation.mutate(activateTarget.id)}
                disabled={activateMutation.isPending}
              >
                {activateMutation.isPending ? "Activating..." : "Activate"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
