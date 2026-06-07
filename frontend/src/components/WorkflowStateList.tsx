import { useState } from "react";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Trash2, AlertCircle } from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import { Toast } from "@/components/Toast";
import { onActivateKeyDown, stopActivationKeyDown } from "@/lib/a11y";
import type { WorkflowState } from "@/types/api";

interface WorkflowStateItemProps {
  state: WorkflowState;
  ticketCount: number;
  statesCount: number;
  disabled?: boolean;
  onDeleteRequest: (stateName: string) => void;
}

function WorkflowStateItem({ state, ticketCount, statesCount, disabled, onDeleteRequest }: Readonly<WorkflowStateItemProps>) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: state.name, disabled });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  // 3-state tooltip: tickets_exist takes priority over last_state (AC-6)
  const isLastState = statesCount <= 1;
  const canDelete = ticketCount === 0 && !isLastState && !disabled;
  const deleteTooltip = ticketCount > 0
    ? "Cannot delete: tickets exist in this state"
    : isLastState
      ? "Cannot delete: last state in workflow"
      : "Delete state";

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-3 rounded-lg border border-hairline bg-surface p-3 shadow-sm ${
        disabled ? "cursor-not-allowed opacity-60" : "cursor-default"
      }`}
    >
      <button
        {...attributes}
        {...listeners}
        className="touch-none rounded p-1 hover:bg-raised"
        aria-label="Drag to reorder"
        disabled={disabled}
      >
        <GripVertical className="h-5 w-5 text-text-muted" />
      </button>

      <div
        className="h-8 w-8 rounded-full"
        style={{ backgroundColor: state.color || "var(--state-backlog)" }}
        aria-hidden="true"
      />

      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-text-primary">{state.name}</span>
          {state.is_initial && (
            <span className="rounded bg-success-soft px-1.5 py-0.5 text-[10px] font-medium text-success">
              Initial
            </span>
          )}
          {state.is_terminal && (
            <span className="rounded bg-raised px-1.5 py-0.5 text-[10px] font-medium text-text-secondary">
              Terminal
            </span>
          )}
        </div>
        <div className="text-xs text-text-muted">
          {ticketCount > 0 ? (
            <span className="flex items-center gap-1">
              <AlertCircle className="h-3 w-3" />
              {ticketCount} ticket{ticketCount !== 1 ? "s" : ""}
            </span>
          ) : (
            "No tickets"
          )}
        </div>
      </div>

      <button
        data-testid={`delete-state-btn-${state.name}`}
        className="rounded p-1.5 text-text-muted hover:bg-danger-soft hover:text-danger disabled:cursor-not-allowed disabled:opacity-30"
        title={deleteTooltip}
        disabled={!canDelete}
        onClick={() => {
          if (canDelete) {
            onDeleteRequest(state.name);
          }
        }}
        aria-label={deleteTooltip}
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

interface WorkflowStateListProps {
  states: WorkflowState[];
  ticketCounts: Record<string, number>;
  onReorder: (states: WorkflowState[]) => void;
  disabled?: boolean;
  workflowId?: string;
  boardKey?: string;
  boardId?: string;
}

export function WorkflowStateList({ states, ticketCounts, onReorder, disabled, workflowId, boardKey, boardId }: Readonly<WorkflowStateListProps>) {
  const [items, setItems] = useState(states);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ variant: "success" | "error"; message: string } | null>(null);
  const qc = useQueryClient();

  // Sync with parent when states prop changes
  useState(() => {
    setItems(states);
  });

  const deleteStateMutation = useMutation({
    mutationFn: ({ stateName }: { stateName: string }) => {
      if (!workflowId) throw new Error("workflowId is required for delete");
      return api.deleteState(workflowId, stateName, boardId);
    },
    onSuccess: (data) => {
      setDeleteTarget(null);
      setDeleteError(null);
      // Invalidate both query keys (PH-102 pattern)
      if (boardKey) {
        qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
        qc.invalidateQueries({ queryKey: ["board", boardKey] });
      }
      const removedMsg = data.removed_transitions > 0
        ? ` (${data.removed_transitions} transition${data.removed_transitions !== 1 ? "s" : ""} removed)`
        : "";
      setToast({
        variant: "success",
        message: `State '${data.state_name}' deleted${removedMsg}`,
      });
    },
    onError: (err: Error) => {
      const reason =
        err instanceof ApiRequestError && err.body && "reason" in err.body
          ? (err.body as Record<string, string>).reason
          : null;
      const reasonMessages: Record<string, string> = {
        tickets_exist: "Bu state'te ticket'lar mevcut; önce ticket'ları başka bir state'e taşıyın.",
        last_state: "Workflow'da en az bir state bulunmalıdır.",
      };
      setDeleteError(
        reason && reason in reasonMessages
          ? (reasonMessages[reason] ?? err.message)
          : err.message,
      );
    },
  });

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      const oldIndex = items.findIndex((s) => s.name === active.id);
      const newIndex = items.findIndex((s) => s.name === over.id);
      const newItems = arrayMove(items, oldIndex, newIndex);
      setItems(newItems);
      onReorder(newItems);
    }
  };

  if (states.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-hairline bg-inset p-8 text-center">
        <p className="text-text-muted">No workflow states defined</p>
        <p className="mt-1 text-sm text-text-muted">Add a state to get started</p>
      </div>
    );
  }

  return (
    <>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={states.map(s => s.name)} strategy={verticalListSortingStrategy}>
          <div className="space-y-2" role="list" aria-label="Workflow states">
            {states.map((state) => (
              <WorkflowStateItem
                key={state.name}
                state={state}
                ticketCount={ticketCounts[state.name] || 0}
                statesCount={states.length}
                disabled={disabled}
                onDeleteRequest={(name) => {
                  setDeleteTarget(name);
                  setDeleteError(null);
                }}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {/* Delete confirmation dialog */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm px-4"
          style={{ background: "var(--bg-overlay)" }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-state-dialog-title"
          onClick={() => {
            if (!deleteStateMutation.isPending) setDeleteTarget(null);
          }}
          onKeyDown={onActivateKeyDown(() => {
            if (!deleteStateMutation.isPending) setDeleteTarget(null);
          })}
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
            onKeyDown={stopActivationKeyDown}
          >
            <h2
              id="delete-state-dialog-title"
              className="text-base font-semibold text-text-primary"
            >
              &apos;{deleteTarget}&apos; state&apos;ini silmek istiyor musunuz?
            </h2>

            <p className="text-sm text-text-secondary">
              Bu işlem geri alınamaz. State&apos;e ait transition&apos;lar da otomatik silinir.
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
                onClick={() => {
                  setDeleteTarget(null);
                  setDeleteError(null);
                }}
                disabled={deleteStateMutation.isPending}
              >
                İptal
              </button>
              <button
                type="button"
                data-testid="confirm-delete-state-btn"
                className="btn text-sm text-text-on-accent"
                style={{ backgroundColor: "var(--danger)" }}
                onClick={() => deleteStateMutation.mutate({ stateName: deleteTarget })}
                disabled={deleteStateMutation.isPending}
              >
                {deleteStateMutation.isPending ? "Siliniyor..." : "Sil"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Success / error toast */}
      {toast && (
        <Toast
          variant={toast.variant}
          message={toast.message}
          onDismiss={() => setToast(null)}
        />
      )}
    </>
  );
}
