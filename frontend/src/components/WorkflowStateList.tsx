import { useState } from "react";
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Trash2, AlertCircle } from "lucide-react";
import type { WorkflowState } from "@/types/api";

interface WorkflowStateItemProps {
  state: WorkflowState;
  ticketCount: number;
  disabled?: boolean;
}

function WorkflowStateItem({ state, ticketCount, disabled }: WorkflowStateItemProps) {
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

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center gap-3 rounded-lg border bg-white p-3 shadow-sm ${
        disabled ? "cursor-not-allowed opacity-60" : "cursor-default"
      }`}
    >
      <button
        {...attributes}
        {...listeners}
        className="touch-none rounded p-1 hover:bg-slate-100"
        aria-label="Drag to reorder"
        disabled={disabled}
      >
        <GripVertical className="h-5 w-5 text-slate-400" />
      </button>

      <div
        className="h-8 w-8 rounded-full"
        style={{ backgroundColor: state.color || "#94a3b8" }}
        aria-hidden="true"
      />

      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-900">{state.name}</span>
          {state.is_initial && (
            <span className="rounded bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700">
              Initial
            </span>
          )}
          {state.is_terminal && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
              Terminal
            </span>
          )}
        </div>
        <div className="text-xs text-slate-500">
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
        className="rounded p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600"
        title={ticketCount > 0 ? "Cannot delete: tickets exist in this state" : "Delete state"}
        disabled={ticketCount > 0 || disabled}
        onClick={() => {
          if (ticketCount === 0 && confirm(`Delete state "${state.name}"?`)) {
            // TODO: Implement delete
          }
        }}
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
}

export function WorkflowStateList({ states, ticketCounts, onReorder, disabled }: WorkflowStateListProps) {
  const [items, setItems] = useState(states);

  // Sync with parent when states prop changes
  useState(() => {
    setItems(states);
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
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
        <p className="text-slate-500">No workflow states defined</p>
        <p className="mt-1 text-sm text-slate-400">Add a state to get started</p>
      </div>
    );
  }

  return (
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
              disabled={disabled}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
