import { useState } from "react";
import { Plus, CheckCircle2, Circle, Zap, AlertTriangle } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
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

  const createMutation = useMutation({
    mutationFn: () => {
      // Prefer active workflow as template; fall back to any workflow; last resort: empty.
      const template = workflows.find((w) => w.is_active) ?? workflows[0];
      const templateStates = template?.states ?? [];
      return api.createWorkflow({
        name: `Workflow ${workflows.length + 1}`,
        states: templateStates as unknown[],
        transitions: [],
        is_default: false,
      });
    },
    onSuccess: (newWorkflow) => {
      qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
      // auto-select the new workflow
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
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
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
        <p className="text-xs text-slate-400 dark:text-slate-500 py-2">
          No workflows found. Create one to get started.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-700 dark:border-slate-700">
          {workflows.map((wf) => (
            <li
              key={wf.id}
              className={`flex items-center justify-between px-3 py-2.5 cursor-pointer transition-colors hover:bg-slate-50 dark:hover:bg-slate-800 ${
                selectedWorkflowId === wf.id
                  ? "bg-indigo-50 dark:bg-indigo-900/20"
                  : ""
              }`}
              onClick={() => onSelect(wf)}
              data-testid={`workflow-row-${wf.id}`}
            >
              <div className="flex items-center gap-2 min-w-0">
                {wf.is_active ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
                ) : (
                  <Circle className="h-3.5 w-3.5 text-slate-300 dark:text-slate-600 shrink-0" />
                )}
                <span className="truncate text-sm text-slate-800 dark:text-slate-200">
                  {wf.name}
                </span>
                {wf.is_active && (
                  <span className="shrink-0 rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
                    active
                  </span>
                )}
              </div>

              {!readOnly && !wf.is_active && (
                <button
                  type="button"
                  data-testid={`activate-btn-${wf.id}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setActivateTarget(wf);
                    setActivateError(null);
                  }}
                  disabled={activateMutation.isPending}
                  className="ml-2 shrink-0 inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium text-indigo-600 ring-1 ring-indigo-200 hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:text-indigo-400 dark:ring-indigo-700 dark:hover:bg-indigo-900/20"
                >
                  <Zap className="h-3 w-3" />
                  Activate
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Activate confirmation dialog */}
      {activateTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="activate-dialog-title"
          onClick={() => setActivateTarget(null)}
        >
          <div
            className="card w-full max-w-sm space-y-4 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h2
              id="activate-dialog-title"
              className="text-base font-semibold dark:text-slate-100"
            >
              Activate &quot;{activateTarget.name}&quot;?
            </h2>

            {orphaned.length > 0 && (
              <div className="rounded-md bg-amber-50 p-3 dark:bg-amber-900/20">
                <div className="flex items-start gap-2 text-amber-800 dark:text-amber-300">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
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
                      Activating may strand these tickets. Confirm to proceed.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {!orphaned.length && (
              <p className="text-sm text-slate-600 dark:text-slate-400">
                The currently active workflow will be deactivated. This change
                takes effect immediately.
              </p>
            )}

            {activateError && (
              <p
                className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-400"
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
