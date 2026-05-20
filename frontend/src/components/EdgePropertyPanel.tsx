import { useState } from "react";
import { X, Check, UserCheck } from "lucide-react";

interface WorkflowTransition {
  from: string;
  to: string;
  allowed_roles?: string[];
}

interface EdgePropertyPanelProps {
  isOpen: boolean;
  edge: WorkflowTransition | null;
  onClose: () => void;
  onApply: (updatedEdge: WorkflowTransition) => void;
  availableRoles: string[];
}

const DEFAULT_ROLES = [
  "pm",
  "architect",
  "backend_dev",
  "frontend_dev",
  "reviewer",
  "qa",
  "admin"
];

export function EdgePropertyPanel({ isOpen, edge, onClose, onApply, availableRoles }: EdgePropertyPanelProps) {
  const [selectedRoles, setSelectedRoles] = useState<string[]>(edge?.allowed_roles ?? []);

  // Reset form when edge changes
  useState(() => {
    if (edge) {
      setSelectedRoles(edge.allowed_roles ?? []);
    }
  });

  // Use available roles from board if provided, otherwise fallback to defaults
  const roleOptions = availableRoles.length > 0 ? availableRoles : DEFAULT_ROLES;

  const handleRoleToggle = (role: string) => {
    setSelectedRoles(prev =>
      prev.includes(role)
        ? prev.filter(r => r !== role)
        : [...prev, role]
    );
  };

  const handleApply = () => {
    if (!edge) return;

    const updatedEdge: WorkflowTransition = {
      ...edge,
      allowed_roles: selectedRoles.length > 0 ? selectedRoles : undefined
    };

    onApply(updatedEdge);
    onClose();
  };

  const handleClose = () => {
    onClose();
  };

  if (!isOpen || !edge) return null;

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
              Transition Properties
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
              {/* Transition Info */}
              <div className="rounded-md bg-slate-50 p-3 dark:bg-slate-700/50">
                <h4 className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                  Transition
                </h4>
                <div className="flex items-center gap-2 text-sm">
                  <span className="rounded bg-slate-200 px-2 py-1 font-mono dark:bg-slate-600">
                    {edge.from}
                  </span>
                  <span className="text-slate-500">→</span>
                  <span className="rounded bg-slate-200 px-2 py-1 font-mono dark:bg-slate-600">
                    {edge.to}
                  </span>
                </div>
              </div>

              {/* Allowed Roles */}
              <div className="space-y-3">
                <div>
                  <span className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    Allowed Roles
                  </span>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    Select which roles can perform this transition. Leave empty to allow all roles.
                  </p>
                </div>

                <div className="space-y-2">
                  {roleOptions.map((role) => (
                    <label key={role} className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={selectedRoles.includes(role)}
                        onChange={() => handleRoleToggle(role)}
                        className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 dark:border-slate-600"
                      />
                      <div className="flex items-center gap-2">
                        <UserCheck className="h-4 w-4 text-slate-400" />
                        <span className="text-sm text-slate-900 dark:text-slate-100 capitalize">
                          {role.replace('_', ' ')}
                        </span>
                      </div>
                    </label>
                  ))}
                </div>

                {/* Selected count */}
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  {selectedRoles.length === 0 ? (
                    "All roles allowed"
                  ) : (
                    `${selectedRoles.length} role${selectedRoles.length === 1 ? '' : 's'} selected`
                  )}
                </div>
              </div>
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