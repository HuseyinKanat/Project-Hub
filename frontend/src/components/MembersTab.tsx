/**
 * PH-39 — MembersTab
 *
 * Full board membership management tab for BoardSettings.
 *
 * Queries:
 *  - useQuery(['members', boardKey])  → GET /api/boards/{board_id}/members
 *  - useQuery(['actors'])             → GET /api/actors (is_active=true actors)
 *
 * Mutations (all optimistic with rollback):
 *  - updateRole → PATCH /api/boards/{board_id}/members/{actor_id}
 *  - removeMember → DELETE /api/boards/{board_id}/members/{actor_id}
 *  - addMember → POST /api/boards/{board_id}/members (via AddMemberModal)
 *
 * A11y:
 *  - <table> with <th scope="col"> for column headers
 *  - Real <select> elements in role cells (not custom dropdowns)
 *  - Remove buttons have aria-label with actor name
 *  - AddMemberModal has focus-trap + ESC-close
 *  - Read-only banner for non-admin (data-testid="members-readonly-banner")
 */

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { UserPlus, Lock } from "lucide-react";
import { api } from "@/api/client";
import { Toast } from "@/components/Toast";
import { MembershipRow } from "@/components/MembershipRow";
import { AddMemberModal } from "@/components/AddMemberModal";
import type { MembershipListResponse, MembershipResponse } from "@/types/api";

export interface MembersTabProps {
  /** Board UUID — used in API calls */
  boardId: string;
  /** Board key (e.g. "PH") — used in query keys + display */
  boardKey: string;
  /**
   * Available roles from board.roles (already normalized to string[]).
   * Pass Object.keys(board.roles?.roles ?? board.roles ?? {})
   */
  availableRoles: string[];
  /**
   * Full board roles object for the permission summary popover.
   * Shape: Record<roleName, { permissions: string[] }>
   */
  boardRoles: Record<string, { permissions: string[] }>;
  /** Whether the current user has admin role on this board */
  isAdmin: boolean;
}

export function MembersTab({
  boardId,
  boardKey,
  availableRoles,
  boardRoles,
  isAdmin,
}: Readonly<MembersTabProps>) {
  const qc = useQueryClient();

  const [toast, setToast] = useState<{
    variant: "success" | "error";
    message: string;
  } | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);

  // ------------------------------------------------------------------
  // Queries
  // ------------------------------------------------------------------
  const membersQuery = useQuery({
    queryKey: ["members", boardKey],
    queryFn: () => api.listBoardMembers(boardId),
    enabled: Boolean(boardId),
  });

  const actorsQuery = useQuery({
    queryKey: ["actors"],
    queryFn: () => api.listActors(),
    enabled: showAddModal, // lazy — only fetch when modal opens
  });

  const members: MembershipResponse[] =
    membersQuery.data?.members ?? [];

  // Last-admin computation: actor ID of the sole admin (if exactly one)
  const adminMembers = members.filter((m) => m.role === "admin");
  const lastAdminId =
    adminMembers.length === 1 ? (adminMembers[0]?.actor.id ?? null) : null;

  // ------------------------------------------------------------------
  // Mutation: update member role (optimistic)
  // ------------------------------------------------------------------
  const updateRoleMutation = useMutation({
    mutationFn: ({ actorId, newRole }: { actorId: string; newRole: string }) =>
      api.updateBoardMember(boardId, actorId, newRole),

    onMutate: async ({ actorId, newRole }) => {
      // Cancel in-flight refetches to avoid overwriting our optimistic update
      await qc.cancelQueries({ queryKey: ["members", boardKey] });
      const snapshot =
        qc.getQueryData<MembershipListResponse>(["members", boardKey]);

      // Optimistic patch
      qc.setQueryData<MembershipListResponse>(["members", boardKey], (old) => {
        if (!old) return old;
        return {
          members: old.members.map((m) =>
            m.actor.id === actorId ? { ...m, role: newRole } : m,
          ),
        };
      });
      return { snapshot };
    },

    onError: (err: Error, _vars, ctx) => {
      // Rollback
      if (ctx?.snapshot) {
        qc.setQueryData(["members", boardKey], ctx.snapshot);
      }
      setToast({
        variant: "error",
        message: err.message || "Failed to update role. Please retry.",
      });
    },

    onSuccess: () => {
      setToast({ variant: "success", message: "Role updated" });
    },

    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["members", boardKey] });
    },
  });

  // ------------------------------------------------------------------
  // Mutation: remove member (optimistic)
  // ------------------------------------------------------------------
  const removeMemberMutation = useMutation({
    mutationFn: (actorId: string) =>
      api.removeBoardMember(boardId, actorId),

    onMutate: async (actorId) => {
      await qc.cancelQueries({ queryKey: ["members", boardKey] });
      const snapshot =
        qc.getQueryData<MembershipListResponse>(["members", boardKey]);

      // Optimistic removal
      qc.setQueryData<MembershipListResponse>(["members", boardKey], (old) => {
        if (!old) return old;
        return {
          members: old.members.filter((m) => m.actor.id !== actorId),
        };
      });
      return { snapshot };
    },

    onError: (err: Error, _vars, ctx) => {
      if (ctx?.snapshot) {
        qc.setQueryData(["members", boardKey], ctx.snapshot);
      }
      setToast({
        variant: "error",
        message: err.message || "Failed to remove member. Please retry.",
      });
    },

    onSuccess: () => {
      setToast({ variant: "success", message: "Member removed" });
    },

    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["members", boardKey] });
    },
  });

  // ------------------------------------------------------------------
  // Handler: add member (via modal onAdd callback)
  // ------------------------------------------------------------------
  const handleAddMember = useCallback(
    async (actorId: string, role: string) => {
      await api.addBoardMember(boardId, actorId, role);
      await qc.invalidateQueries({ queryKey: ["members", boardKey] });
      setToast({ variant: "success", message: "Member added" });
    },
    [boardId, boardKey, qc],
  );

  // ------------------------------------------------------------------
  // Loading state
  // ------------------------------------------------------------------
  if (membersQuery.isLoading) {
    return (
      <div className="space-y-2" aria-label="Members loading" aria-busy="true">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-12 animate-pulse rounded bg-raised"
          />
        ))}
      </div>
    );
  }

  if (membersQuery.isError) {
    return (
      <div
        className="rounded-md bg-danger-soft px-4 py-3 text-sm text-danger"
        role="alert"
      >
        Failed to load members:{" "}
        {membersQuery.error instanceof Error
          ? membersQuery.error.message
          : "Unknown error"}
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <>
      {/* Toast */}
      {toast && (
        <Toast
          variant={toast.variant}
          message={toast.message}
          onDismiss={() => setToast(null)}
          durationMs={3000}
        />
      )}

      {/* Read-only banner (non-admin) */}
      {!isAdmin && (
        <div
          className="mb-4 flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
          role="note"
          data-testid="members-readonly-banner"
        >
          <Lock className="h-4 w-4 shrink-0" />
          <span>Read-only — admin role required to manage members.</span>
        </div>
      )}

      {/* Header: member count + Add button */}
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-text-secondary">
          {members.length} member{members.length !== 1 ? "s" : ""}
        </p>
        {isAdmin && (
          <button
            type="button"
            onClick={() => setShowAddModal(true)}
            className="btn btn-primary inline-flex items-center gap-1.5 py-1.5 text-sm"
            data-testid="add-member-btn"
          >
            <UserPlus className="h-4 w-4" />
            Add Member
          </button>
        )}
      </div>

      {/* Membership table */}
      {members.length === 0 ? (
        <div
          className="flex items-center justify-center rounded-md border border-dashed border-hairline py-8 text-sm text-text-muted"
          data-testid="members-empty"
        >
          No members found on this board.
        </div>
      ) : (
        <div
          className="overflow-x-auto rounded-md border border-hairline"
          data-testid="members-table-container"
        >
          <table
            className="min-w-full text-sm"
            aria-label="Board members roster"
          >
            <thead>
              <tr className="bg-raised">
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-text-muted"
                >
                  Actor
                </th>
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-text-muted"
                >
                  Role
                </th>
                <th
                  scope="col"
                  className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-text-muted"
                >
                  Permissions
                </th>
                {isAdmin && (
                  <th
                    scope="col"
                    className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-text-muted"
                  >
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <MembershipRow
                  key={member.actor.id}
                  member={member}
                  availableRoles={availableRoles}
                  boardRoles={boardRoles}
                  isLastAdmin={lastAdminId === member.actor.id}
                  isAdmin={isAdmin}
                  boardKey={boardKey}
                  onRoleChange={(actorId, newRole) =>
                    updateRoleMutation.mutate({ actorId, newRole })
                  }
                  onRemove={(actorId) =>
                    removeMemberMutation.mutate(actorId)
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Member Modal */}
      {showAddModal && (
        <AddMemberModal
          boardId={boardId}
          boardKey={boardKey}
          actors={actorsQuery.data?.actors ?? []}
          members={members}
          availableRoles={availableRoles}
          onAdd={handleAddMember}
          onClose={() => setShowAddModal(false)}
        />
      )}
    </>
  );
}
