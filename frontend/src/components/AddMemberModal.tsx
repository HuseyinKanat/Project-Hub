/**
 * PH-39 — AddMemberModal
 *
 * Modal for adding an existing actor to the board with a specific role.
 * Only shows actors NOT already in the member roster (un-joined actors).
 *
 * A11y:
 *  - role="dialog" aria-modal="true" aria-labelledby="add-member-title"
 *  - Focus trapped inside modal: first input auto-focused
 *  - ESC closes the modal
 *  - Real <select> elements (not custom dropdowns)
 */

import { useRef, useEffect, useState, FormEvent } from "react";
import { X } from "lucide-react";
import type { ActorSummary, MembershipResponse } from "@/types/api";
import { onActivateKeyDown, stopActivationKeyDown } from "@/lib/a11y";

export interface AddMemberModalProps {
  /** Board UUID (for API call) */
  boardId: string;
  /** Board key (for display e.g. "PH") */
  boardKey: string;
  /** All is_active actors from /api/actors */
  actors: ActorSummary[];
  /** Current board members (to exclude already-joined actors) */
  members: MembershipResponse[];
  /** Available roles (Object.keys of board.roles) */
  availableRoles: string[];
  /** Called with actor_id + role when form is submitted successfully */
  onAdd: (actorId: string, role: string) => Promise<void>;
  /** Called when modal should close */
  onClose: () => void;
}

export function AddMemberModal({
  boardId: _boardId,
  boardKey,
  actors,
  members,
  availableRoles,
  onAdd,
  onClose,
}: AddMemberModalProps) {
  // Filter out actors already on the board
  const unjoined = actors.filter(
    (a) => !members.some((m) => m.actor.id === a.id),
  );

  const [selectedActorId, setSelectedActorId] = useState<string>(
    unjoined[0]?.id ?? "",
  );
  const [selectedRole, setSelectedRole] = useState<string>(
    availableRoles[0] ?? "",
  );

  // When actors load asynchronously (lazy query), seed the initial selection
  // if we started with an empty list
  useEffect(() => {
    if (selectedActorId === "" && unjoined.length > 0 && unjoined[0]) {
      setSelectedActorId(unjoined[0].id);
    }
  }, [unjoined, selectedActorId]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);

  // Focus trap: focus the first select on mount
  const actorSelectRef = useRef<HTMLSelectElement>(null);
  useEffect(() => {
    actorSelectRef.current?.focus();
  }, []);

  // ESC closes the modal
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!selectedActorId || !selectedRole) return;

    setIsSubmitting(true);
    setInlineError(null);
    try {
      await onAdd(selectedActorId, selectedRole);
      onClose();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to add member";
      setInlineError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 backdrop-blur-sm"
      style={{ background: "var(--bg-overlay)" }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-member-title"
      data-testid="add-member-modal"
      onClick={onClose}
      onKeyDown={onActivateKeyDown(onClose)}
    >
      <form
        onSubmit={handleSubmit}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={stopActivationKeyDown}
        className="card w-full max-w-md space-y-4 p-6"
        style={{
          background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--hairline-cyan)",
          boxShadow: "var(--shadow-glass)",
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2
            id="add-member-title"
            className="text-lg font-semibold text-text-primary"
          >
            Add Member to {boardKey}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 hover:bg-raised"
            aria-label="Close add member modal"
          >
            <X className="h-5 w-5 text-text-muted" />
          </button>
        </div>

        {/* Inline error */}
        {inlineError && (
          <div
            className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
            role="alert"
            data-testid="add-member-error"
          >
            {inlineError}
          </div>
        )}

        {/* Actor select */}
        {unjoined.length === 0 ? (
          <p className="text-sm text-text-muted">
            All active actors are already members of this board.
          </p>
        ) : (
          <>
            <label className="block space-y-1.5">
              <span className="text-sm font-medium text-text-secondary">
                Actor
              </span>
              <select
                ref={actorSelectRef}
                value={selectedActorId}
                onChange={(e) => setSelectedActorId(e.target.value)}
                className="input w-full"
                required
                aria-label="Select actor to add"
                data-testid="actor-select"
              >
                {unjoined.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name}
                    {a.kind === "agent" && a.agent_role_hint
                      ? ` (${a.agent_role_hint})`
                      : ""}
                  </option>
                ))}
              </select>
            </label>

            {/* Role select */}
            <label className="block space-y-1.5">
              <span className="text-sm font-medium text-text-secondary">
                Role
              </span>
              <select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value)}
                className="input w-full"
                required
                aria-label="Select role"
                data-testid="role-select"
              >
                {availableRoles.map((r) => (
                  <option key={r} value={r}>
                    {r.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </label>

            {/* Buttons */}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="btn btn-secondary"
                disabled={isSubmitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={isSubmitting || !selectedActorId || !selectedRole}
                data-testid="add-member-submit"
              >
                {isSubmitting ? "Adding..." : "Add Member"}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
