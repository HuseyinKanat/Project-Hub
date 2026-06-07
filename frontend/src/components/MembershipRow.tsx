/**
 * PH-39 — MembershipRow
 *
 * One row in the membership roster table.
 *
 * Props:
 *  - member: MembershipResponse
 *  - availableRoles: string[] (Object.keys of board.roles)
 *  - boardRoles: Record<string, { permissions: string[] }> (for permission summary popover)
 *  - isLastAdmin: boolean — disables role change + remove button
 *  - isAdmin: boolean (current user is admin) — controls editability
 *  - onRoleChange(actorId, newRole): called when user changes the role select
 *  - onRemove(actorId): called after confirm dialog OK
 *
 * A11y:
 *  - Role cell is a real <select> (admin) or <span> (non-admin/read-only)
 *  - Remove button has aria-label including the actor name
 *  - isLastAdmin: Remove button has title tooltip "Cannot remove the last admin"
 */

import { useState } from "react";
import { Trash2, ChevronDown, ChevronUp } from "lucide-react";
import type { MembershipResponse } from "@/types/api";

/**
 * Recipe C — static text-role-* map (JIT-safe; no templated `text-role-${x}`
 * which Tailwind would purge). Mirrors TicketDetail ROLE_META. Accepts both
 * the bare role and the `_dev` agent_role_hint variants.
 */
const ROLE_TEXT: Record<string, string> = {
  admin: "text-role-admin",
  pm: "text-role-pm",
  architect: "text-role-architect",
  backend: "text-role-backend",
  backend_dev: "text-role-backend",
  frontend: "text-role-frontend",
  frontend_dev: "text-role-frontend",
  reviewer: "text-role-reviewer",
  qa: "text-role-qa",
  orchestrator: "text-role-orchestrator",
};

function roleTextClass(role: string): string {
  return ROLE_TEXT[role] ?? "text-text-secondary";
}

export interface MembershipRowProps {
  member: MembershipResponse;
  availableRoles: string[];
  boardRoles: Record<string, { permissions: string[] }>;
  isLastAdmin: boolean;
  isAdmin: boolean;
  boardKey: string;
  onRoleChange: (actorId: string, newRole: string) => void;
  onRemove: (actorId: string) => void;
}

export function MembershipRow({
  member,
  availableRoles,
  boardRoles,
  isLastAdmin,
  isAdmin,
  boardKey,
  onRoleChange,
  onRemove,
}: Readonly<MembershipRowProps>) {
  const { actor, role } = member;
  const [showPermissions, setShowPermissions] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const permissions: string[] =
    (boardRoles[role] as { permissions?: string[] } | undefined)?.permissions ??
    [];

  function handleRoleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    if (!isAdmin || isLastAdmin) return;
    onRoleChange(actor.id, e.target.value);
  }

  function handleRemoveClick() {
    setShowConfirm(true);
  }

  function handleConfirmRemove() {
    setShowConfirm(false);
    onRemove(actor.id);
  }

  return (
    <>
      <tr
        className="border-t border-hairline hover:bg-raised/40"
        data-testid={`member-row-${actor.id}`}
      >
        {/* Name + kind badge */}
        <td className="px-4 py-3">
          <div className="flex flex-col gap-0.5">
            <span
              className={`font-medium text-text-primary text-sm ${
                actor.kind === "agent" ? "mono" : ""
              }`}
            >
              {actor.display_name}
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className={`inline-block rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                  actor.kind === "agent"
                    ? "bg-accent-soft text-accent"
                    : "bg-success-soft text-success"
                }`}
                data-testid={`kind-badge-${actor.id}`}
              >
                {actor.kind === "agent" ? "Agent" : "Human"}
              </span>
              {actor.kind === "agent" && actor.agent_role_hint && (
                <span className="text-[11px] text-text-muted">
                  {actor.agent_role_hint}
                </span>
              )}
            </div>
          </div>
        </td>

        {/* Role cell */}
        <td className="px-4 py-3">
          {isAdmin && !isLastAdmin ? (
            <select
              value={role}
              onChange={handleRoleChange}
              className="input py-1 text-sm"
              aria-label={`Role for ${actor.display_name}`}
              data-testid={`role-select-${actor.id}`}
            >
              {availableRoles.map((r) => (
                <option key={r} value={r}>
                  {r.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          ) : (
            <span
              className={`text-sm font-medium ${roleTextClass(role)}`}
              data-testid={`role-text-${actor.id}`}
            >
              {role.replace(/_/g, " ")}
              {isLastAdmin && (
                <span className="ml-1.5 text-[10px] text-warning">
                  (last admin)
                </span>
              )}
            </span>
          )}
        </td>

        {/* Permission summary */}
        <td className="px-4 py-3">
          <button
            type="button"
            onClick={() => setShowPermissions((v) => !v)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-text-muted hover:bg-raised"
            aria-expanded={showPermissions}
            aria-label={`Toggle permissions for role ${role}`}
          >
            {permissions.length} perm{permissions.length !== 1 ? "s" : ""}
            {showPermissions ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
          </button>
          {showPermissions && permissions.length > 0 && (
            <ul className="mt-1 max-h-24 overflow-y-auto text-[11px] text-text-secondary">
              {permissions.map((p) => (
                <li key={p} className="mono">
                  {p}
                </li>
              ))}
            </ul>
          )}
          {showPermissions && permissions.length === 0 && (
            <p className="mt-1 text-[11px] text-text-muted">
              No permissions defined for this role.
            </p>
          )}
        </td>

        {/* Remove button (admin only) */}
        <td className="px-4 py-3 text-right">
          {isAdmin && (
            <button
              type="button"
              onClick={handleRemoveClick}
              disabled={isLastAdmin}
              title={isLastAdmin ? "Cannot remove the last admin" : undefined}
              aria-label={`Remove ${actor.display_name} from board`}
              data-testid={`remove-btn-${actor.id}`}
              className={`rounded p-1.5 transition-colors ${
                isLastAdmin
                  ? "cursor-not-allowed text-text-muted opacity-50"
                  : "text-text-muted hover:bg-danger-soft hover:text-danger"
              }`}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </td>
      </tr>

      {/* Confirm dialog row */}
      {showConfirm && (
        <tr>
          <td
            colSpan={4}
            className="bg-warning-soft px-4 py-3"
          >
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm text-warning">
                Remove <strong>{actor.display_name}</strong> from board{" "}
                <strong>{boardKey}</strong>?
              </span>
              <div className="flex gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => setShowConfirm(false)}
                  className="btn btn-secondary py-1 text-xs"
                  data-testid={`confirm-cancel-${actor.id}`}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleConfirmRemove}
                  className="rounded px-3 py-1 text-xs font-medium text-text-on-accent"
                  style={{ backgroundColor: "var(--danger)" }}
                  data-testid={`confirm-remove-${actor.id}`}
                >
                  Remove
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
