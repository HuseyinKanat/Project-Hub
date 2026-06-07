import { useEffect, useState } from "react";
import { X, Check, AlertCircle } from "lucide-react";
import { onActivateKeyDown } from "@/lib/a11y";
import type { WorkflowState } from "@/types/api";

interface NodePropertyPanelProps {
  isOpen: boolean;
  node: WorkflowState | null;
  onClose: () => void;
  onApply: (updatedNode: WorkflowState) => void;
  existingNodeNames: string[];
}

export function NodePropertyPanel({ isOpen, node, onClose, onApply, existingNodeNames }: Readonly<NodePropertyPanelProps>) {
  const [name, setName] = useState(node?.name ?? "");
  const [color, setColor] = useState(node?.color ?? "#8b5cf6");
  const [isInitial, setIsInitial] = useState(node?.is_initial ?? false);
  const [isTerminal, setIsTerminal] = useState(node?.is_terminal ?? false);
  const [error, setError] = useState<string | null>(null);

  // Reset form whenever the selected node changes (fixes useState lazy-init bug)
  useEffect(() => {
    if (node) {
      setName(node.name);
      setColor(node.color ?? "#8b5cf6");
      setIsInitial(node.is_initial ?? false);
      setIsTerminal(node.is_terminal ?? false);
      setError(null);
    }
  }, [node]);

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
        className="fixed inset-0 z-40 backdrop-blur-sm"
        style={{ background: "var(--bg-overlay)" }}
        onClick={handleClose}
        onKeyDown={onActivateKeyDown(handleClose)}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 z-50 h-full w-80 bg-surface shadow-lg border-l border-hairline">
        <div className="flex h-full flex-col">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
            <h3 className="text-lg font-semibold text-text-primary">
              State Properties
            </h3>
            <button
              onClick={handleClose}
              className="rounded-md p-1 hover:bg-raised"
            >
              <X className="h-5 w-5 text-text-muted" />
            </button>
          </div>

          {/* Form */}
          <div className="flex-1 overflow-y-auto p-4">
            <div className="space-y-4">
              {/* Name */}
              <label className="block space-y-2">
                <span className="text-sm font-medium text-text-secondary">
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
                <span className="text-sm font-medium text-text-secondary">
                  Color
                </span>
                <div className="flex items-center gap-3">
                  <input
                    type="color"
                    value={color}
                    onChange={(e) => setColor(e.target.value)}
                    className="h-10 w-16 cursor-pointer rounded border border-hairline"
                  />
                  <code className="mono rounded bg-inset px-2 py-1 text-sm text-text-secondary">
                    {color}
                  </code>
                </div>
              </label>

              {/* State Type Toggles */}
              <div className="space-y-3">
                <span className="text-sm font-medium text-text-secondary">
                  State Type
                </span>

                <label className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={isInitial}
                    onChange={(e) => setIsInitial(e.target.checked)}
                    className="rounded border-hairline accent-[var(--accent)]"
                  />
                  <div>
                    <span className="text-sm text-text-primary">Initial State</span>
                    <p className="text-xs text-text-muted">
                      New tickets start in this state
                    </p>
                  </div>
                </label>

                <label className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={isTerminal}
                    onChange={(e) => setIsTerminal(e.target.checked)}
                    className="rounded border-hairline accent-[var(--accent)]"
                  />
                  <div>
                    <span className="text-sm text-text-primary">Terminal State</span>
                    <p className="text-xs text-text-muted">
                      No outgoing transitions allowed
                    </p>
                  </div>
                </label>
              </div>

              {/* Error */}
              {error && (
                <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">
                  <AlertCircle className="mr-2 inline h-4 w-4" />
                  {error}
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="flex justify-end gap-2 border-t border-hairline px-4 py-3">
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