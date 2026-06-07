import { useState, useEffect } from "react";
import { X, Check, UserCheck, Lock, Shield, Trash2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { onActivateKeyDown } from "@/lib/a11y";
import type { FieldGates, WorkflowTransition } from "@/types/api";

interface EdgePropertyPanelProps {
  isOpen: boolean;
  edge: WorkflowTransition | null;
  onClose: () => void;
  /** Legacy local-save callback — only used when workflowId is null (no MCP persist available) */
  onApply: (updatedEdge: WorkflowTransition) => void;
  /** Called when the user confirms deletion of this transition. */
  onDelete?: (edge: WorkflowTransition) => void;
  availableRoles: string[];
  /** Required to persist via MCP. If null, falls back to local onApply. */
  workflowId: string | null;
  /** Used to invalidate the workflows cache after Apply succeeds. */
  boardKey: string;
  /** Board UUID — required by add_transition upsert to enforce board isolation (PH-97). */
  boardId?: string;
  /** When true, the Apply button is hidden and inputs are disabled. */
  readOnly?: boolean;
  /** Called when applyTransitionMutation succeeds, before onClose. Parent can show a toast. */
  onApplySuccess?: () => void;
}

const DEFAULT_ROLES = [
  "pm",
  "architect",
  "backend_dev",
  "frontend_dev",
  "reviewer",
  "qa",
  "admin",
];

const FIELD_OPTIONS = [
  "technical_depth",
  "impact_analysis",
  "test_plan",
  "acceptance_criteria",
  "description",
  "expected_behavior",
  "actual_behavior",
  "steps_to_reproduce",
];

const TICKET_TYPE_OPTIONS = ["feature", "bug", "task", "epic"];

export function EdgePropertyPanel({
  isOpen,
  edge,
  onClose,
  onApply,
  onDelete,
  availableRoles,
  workflowId,
  boardKey,
  boardId,
  readOnly = false,
  onApplySuccess,
}: Readonly<EdgePropertyPanelProps>) {
  const qc = useQueryClient();
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [selectedRequiredFields, setSelectedRequiredFields] = useState<string[]>([]);
  const [selectedExemptTypes, setSelectedExemptTypes] = useState<string[]>([]);
  const [applyError, setApplyError] = useState<string | null>(null);

  // Reset all form state whenever the selected edge changes (fixes latent useState lazy-init bug)
  useEffect(() => {
    if (edge) {
      setSelectedRoles(edge.allowed_roles ?? []);
      setSelectedRequiredFields(edge.field_gates?.required_fields ?? []);
      setSelectedExemptTypes(edge.field_gates?.exempt_ticket_types ?? []);
    } else {
      setSelectedRoles([]);
      setSelectedRequiredFields([]);
      setSelectedExemptTypes([]);
    }
    setApplyError(null);
  }, [edge]);

  // Single addTransition call (upsert — backend now idempotent after PH-99).
  // Replaces the old two-call pattern (setFieldGates + addTransition with silent try/catch).
  const applyTransitionMutation = useMutation({
    mutationFn: async ({
      from,
      to,
      fieldGates,
      allowedRoles,
    }: {
      from: string;
      to: string;
      fieldGates: FieldGates;
      allowedRoles: string[];
    }) => {
      if (!workflowId) throw new Error("No workflow selected");
      // addTransition is now upsert — always safe to call on existing transitions.
      // Errors are propagated to onError (no silent swallow).
      await api.addTransition(workflowId, from, to, allowedRoles, fieldGates, boardId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
      setApplyError(null);
      // Notify parent BEFORE closing so the toast state is set while panel is visible
      onApplySuccess?.();
      onClose();
    },
    onError: (err: Error) => {
      setApplyError(err.message);
    },
  });

  // Use available roles from board if provided, otherwise fallback to defaults
  const roleOptions = availableRoles.length > 0 ? availableRoles : DEFAULT_ROLES;

  const handleRoleToggle = (role: string) => {
    if (readOnly) return;
    setSelectedRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role],
    );
  };

  const handleFieldToggle = (field: string) => {
    if (readOnly) return;
    setSelectedRequiredFields((prev) =>
      prev.includes(field) ? prev.filter((f) => f !== field) : [...prev, field],
    );
  };

  const handleExemptTypeToggle = (type: string) => {
    if (readOnly) return;
    setSelectedExemptTypes((prev) =>
      prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type],
    );
  };

  const handleApply = () => {
    if (!edge || readOnly) return;

    // Always send explicit arrays so backend does a full replace (PUT semantic).
    // Empty array = "clear all gates for this key" — consistent with upsert semantics.
    const fieldGates: FieldGates = {
      required_fields: selectedRequiredFields,
      exempt_ticket_types: selectedExemptTypes,
    };

    if (workflowId) {
      // Persist via MCP (preferred path) — single upsert call for field_gates + allowed_roles
      applyTransitionMutation.mutate({ from: edge.from, to: edge.to, fieldGates, allowedRoles: selectedRoles });
    } else {
      // Fallback: local-only save (no MCP)
      const updatedEdge: WorkflowTransition = {
        ...edge,
        allowed_roles: selectedRoles.length > 0 ? selectedRoles : undefined,
        field_gates: fieldGates,
      };
      onApply(updatedEdge);
      onClose();
    }
  };

  if (!isOpen || !edge) return null;

  const isPending = applyTransitionMutation.isPending;

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-40 backdrop-blur-sm"
        style={{ background: "var(--bg-overlay)" }}
        onClick={onClose}
        onKeyDown={onActivateKeyDown(onClose)}
      />

      {/* Panel */}
      <div
        className="fixed right-0 top-0 z-50 h-full w-80 bg-surface shadow-lg border-l border-hairline"
        data-testid="edge-property-panel"
      >
        <div className="flex h-full flex-col">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
            <h3 className="text-lg font-semibold text-text-primary">
              Transition Properties
            </h3>
            <button
              onClick={onClose}
              className="rounded-md p-1 hover:bg-raised"
              aria-label="Close panel"
            >
              <X className="h-5 w-5 text-text-muted" />
            </button>
          </div>

          {/* Read-only notice */}
          {readOnly && (
            <div className="flex items-center gap-2 border-b border-hairline bg-warning-soft px-4 py-2">
              <Shield className="h-4 w-4 shrink-0 text-warning" />
              <p className="text-xs text-warning">
                Read-only — admin/pm role required to edit.
              </p>
            </div>
          )}

          {/* Form */}
          <div className="flex-1 overflow-y-auto p-4">
            <div className="space-y-6">
              {/* Transition Info */}
              <div className="rounded-md bg-inset p-3">
                <h4 className="mb-2 text-sm font-medium text-text-secondary">
                  Transition
                </h4>
                <div className="flex items-center gap-2 text-sm">
                  <span className="mono rounded bg-inset px-2 py-1 text-accent border border-hairline">
                    {edge.from}
                  </span>
                  <span className="text-text-muted">→</span>
                  <span className="mono rounded bg-inset px-2 py-1 text-accent border border-hairline">
                    {edge.to}
                  </span>
                </div>
              </div>

              {/* Required Fields (field gates) */}
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium text-text-secondary">
                  <span className="flex items-center gap-1.5">
                    <Lock className="h-3.5 w-3.5 text-text-muted" />
                    Required Fields
                  </span>
                </legend>
                <p className="text-xs text-text-muted">
                  These ticket fields must be non-empty before this transition
                  is allowed.
                </p>
                <div className="space-y-2" data-testid="required-fields-list">
                  {FIELD_OPTIONS.map((field) => (
                    <label key={field} className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={selectedRequiredFields.includes(field)}
                        onChange={() => handleFieldToggle(field)}
                        disabled={readOnly}
                        className="rounded border-hairline accent-[var(--accent)] disabled:opacity-50"
                        data-testid={`required-field-${field}`}
                      />
                      <span className="text-sm font-mono text-text-primary">
                        {field}
                      </span>
                    </label>
                  ))}
                </div>
                <div className="text-xs text-text-muted">
                  {selectedRequiredFields.length === 0
                    ? "No required fields (all tickets may transition)"
                    : `${selectedRequiredFields.length} field${selectedRequiredFields.length === 1 ? "" : "s"} required`}
                </div>
              </fieldset>

              {/* Exempt Ticket Types */}
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium text-text-secondary">
                  Exempt Ticket Types
                </legend>
                <p className="text-xs text-text-muted">
                  Tickets of these types skip the required-fields check for
                  this transition.
                </p>
                <div className="space-y-2" data-testid="exempt-types-list">
                  {TICKET_TYPE_OPTIONS.map((type) => (
                    <label key={type} className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={selectedExemptTypes.includes(type)}
                        onChange={() => handleExemptTypeToggle(type)}
                        disabled={readOnly}
                        className="rounded border-hairline accent-[var(--accent)] disabled:opacity-50"
                        data-testid={`exempt-type-${type}`}
                      />
                      <span className="text-sm capitalize text-text-primary">
                        {type}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              {/* Allowed Roles */}
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium text-text-secondary">
                  <span className="flex items-center gap-1.5">
                    <UserCheck className="h-3.5 w-3.5 text-text-muted" />
                    Allowed Roles
                  </span>
                </legend>
                <p className="text-xs text-text-muted">
                  Roles that can perform this transition. Leave empty to allow
                  all roles.
                </p>

                <div className="space-y-2">
                  {roleOptions.map((role) => (
                    <label key={role} className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={selectedRoles.includes(role)}
                        onChange={() => handleRoleToggle(role)}
                        disabled={readOnly}
                        className="rounded border-hairline accent-[var(--accent)] disabled:opacity-50"
                      />
                      <span className="text-sm capitalize text-text-primary">
                        {role.replace("_", " ")}
                      </span>
                    </label>
                  ))}
                </div>

                <div className="text-xs text-text-muted">
                  {selectedRoles.length === 0
                    ? "All roles allowed"
                    : `${selectedRoles.length} role${selectedRoles.length === 1 ? "" : "s"} selected`}
                </div>
              </fieldset>

              {/* Error */}
              {applyError && (
                <p
                  className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger"
                  role="alert"
                >
                  {applyError}
                </p>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between border-t border-hairline px-4 py-3">
            {!readOnly && onDelete && edge ? (
              <button
                type="button"
                data-testid="edge-delete-btn"
                className="inline-flex items-center rounded-md px-3 py-1.5 text-sm font-medium text-danger ring-1 ring-danger hover:bg-danger-soft"
                onClick={() => onDelete(edge)}
                disabled={isPending}
                aria-label="Delete transition"
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                Delete
              </button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={onClose}
                disabled={isPending}
              >
                Cancel
              </button>
              {!readOnly && (
                <button
                  type="button"
                  data-testid="edge-apply-btn"
                  className="btn-primary inline-flex items-center text-sm"
                  onClick={handleApply}
                  disabled={isPending}
                >
                  <Check className="mr-2 h-4 w-4" />
                  {isPending ? "Saving..." : "Apply Changes"}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
