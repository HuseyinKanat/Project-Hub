import { useState } from "react";
import { X, Check, AlertCircle } from "lucide-react";
import type { WorkflowState } from "@/types/api";

interface NodePropertyPanelProps {
  isOpen: boolean;
  node: WorkflowState | null;
  onClose: () => void;
  onApply: (updatedNode: WorkflowState) => void;
  existingNodeNames: string[];
}

export function NodePropertyPanel({ isOpen, node, onClose, onApply, existingNodeNames }: NodePropertyPanelProps) {
  const [name, setName] = useState(node?.name ?? "");
  const [color, setColor] = useState(node?.color ?? "#8b5cf6");
  const [isInitial, setIsInitial] = useState(node?.is_initial ?? false);
  const [isTerminal, setIsTerminal] = useState(node?.is_terminal ?? false);
  const [error, setError] = useState<string | null>(null);

  // Reset form when node changes
  useState(() => {
    if (node) {
      setName(node.name);
      setColor(node.color ?? "#8b5cf6");
      setIsInitial(node.is_initial ?? false);
      setIsTerminal(node.is_terminal ?? false);
      setError(null);
    }
  });

  const handleApply = () => {
    if (!node) return;

    // Validation
    if (!name.trim()) {
      setError("State name is required");
      return;
    }

    // Check for duplicate names (excluding current node)
    if (name !== node.name && existingNodeNames.some(existing =>
      existing.toLowerCase() === name.trim().toLowerCase()
    )) {
      setError("A state with this name already exists");
      return;
    }

    const updatedNode: WorkflowState = {
      ...node,
      name: name.trim(),
      color,
      is_initial: isInitial,
      is_terminal: isTerminal
    };

    onApply(updatedNode);
    setError(null);
    onClose();
  };

  const handleClose = () => {
    setError(null);
    onClose();
  };

  if (!isOpen || !node) return null;

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-40 bg-slate-900/60 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 z-50 h-full w-80 bg-white shadow-2xl dark:bg-slate-800 border-l border-slate-200 dark:border-slate-700">
        <div className="flex h-full flex-col">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700">
            <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              State Properties
            </h3>
            <button
              onClick={handleClose}
              className="rounded-md p-1 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              <X className="h-5 w-5 text-slate-500 dark:text-slate-400" />
            </button>
          </div>

          {/* Form */}
          <div className="flex-1 overflow-y-auto p-4">
            <div className="space-y-4">
              {/* Name */}
              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  State Name
                </span>
                <input
                  type="text"
                  className="input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g., In Review"
                />
              </label>

              {/* Color */}
              <label className="block space-y-2">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  Color
                </span>
                <div className="flex items-center gap-3">
                  <input
                    type="color"
                    value={color}
                    onChange={(e) => setColor(e.target.value)}
                    className="h-10 w-16 cursor-pointer rounded border border-slate-300 dark:border-slate-600"
                  />
                  <code className="rounded bg-slate-100 px-2 py-1 text-sm dark:bg-slate-700 dark:text-slate-300">
                    {color}
                  </code>
                </div>
              </label>

              {/* State Type Toggles */}
              <div className="space-y-3">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
                  State Type
                </span>

                <label className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={isInitial}
                    onChange={(e) => setIsInitial(e.target.checked)}
                    className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 dark:border-slate-600"
                  />
                  <div>
                    <span className="text-sm text-slate-900 dark:text-slate-100">Initial State</span>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      New tickets start in this state
                    </p>
                  </div>
                </label>

                <label className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={isTerminal}
                    onChange={(e) => setIsTerminal(e.target.checked)}
                    className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 dark:border-slate-600"
                  />
                  <div>
                    <span className="text-sm text-slate-900 dark:text-slate-100">Terminal State</span>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      No outgoing transitions allowed
                    </p>
                  </div>
                </label>
              </div>

              {/* Error */}
              {error && (
                <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400" role="alert">
                  <AlertCircle className="mr-2 inline h-4 w-4" />
                  {error}
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3 dark:border-slate-700">
            <button
              type="button"
              className="btn-ghost text-sm"
              onClick={handleClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary inline-flex items-center text-sm"
              onClick={handleApply}
              disabled={!name.trim()}
            >
              <Check className="mr-2 h-4 w-4" />
              Apply Changes
            </button>
          </div>
        </div>
      </div>
    </>
  );
}