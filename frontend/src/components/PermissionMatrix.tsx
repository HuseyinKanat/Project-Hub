/**
 * PH-38 — PermissionMatrix
 *
 * Renders a transitions × roles checkbox grid.
 * Rows = workflow transitions ("from → to"), columns = board roles.
 * Each cell is a real <input type="checkbox"> that toggles the role in/out of
 * the transition's allowed_roles via api.addTransition (upsert — same call as
 * EdgePropertyPanel).
 *
 * Design: mirrors EdgePropertyPanel.applyTransitionMutation (lines 86-113) for
 * the optimistic-update + onError rollback + onSettled invalidation pattern.
 *
 * A11y: <table> + <th scope="row"> / <th scope="col"> + real checkboxes with
 *        aria-label; keyboard Tab/Space fully reachable.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { api } from "@/api/client";
import { Toast } from "./Toast";
import type { WorkflowTransition } from "@/types/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PermissionMatrixProps {
  /** Workflow UUID — null when no workflow is selected (renders empty state). */
  workflowId: string | null;
  /** Full transition list from the active workflow. */
  transitions: WorkflowTransition[];
  /**
   * Roles available on this board (from board.roles keys).
   * Orphan roles (appear in transition.allowed_roles but NOT in this list)
   * are shown as gray-disabled with a tooltip.
   */
  availableRoles: string[];
  /** Board key — used for TanStack Query invalidation key. */
  boardKey: string;
  /** Board UUID — forwarded to addTransition for board isolation guard. */
  boardId?: string;
  /** When true, all checkboxes are disabled and a read-only banner is shown. */
  readOnly?: boolean;
  /**
   * When true, renders a pulsing skeleton (parent workflowsQuery.isLoading).
   * The number of skeleton rows defaults to 4 when no transitions are provided.
   */
  isLoading?: boolean;
}

// ---------------------------------------------------------------------------
// Normalize helper
// ---------------------------------------------------------------------------

/** Treat null / undefined / [] identically as "wildcard / all roles allowed". */
function normalizeRoles(roles: string[] | null | undefined): string[] {
  if (!roles || roles.length === 0) return [];
  return roles;
}

/** Build a stable row-key for a transition. */
function rowKey(t: WorkflowTransition): string {
  return `${t.from}__${t.to}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PermissionMatrix({
  workflowId,
  transitions,
  availableRoles,
  boardKey,
  boardId,
  readOnly = false,
  isLoading = false,
}: Readonly<PermissionMatrixProps>) {
  const qc = useQueryClient();

  // ------------------------------------------------------------------
  // Toast state (same pattern as WorkflowEditor lines 656-663)
  // ------------------------------------------------------------------
  const [toast, setToast] = useState<{
    variant: "success" | "error";
    message: string;
  } | null>(null);

  // ------------------------------------------------------------------
  // Local transitions mirror — seeded from props, updated optimistically.
  // Reseeds whenever props.transitions changes (mirrors WorkflowEditor
  // useEffect([initialEdges]) at line 301).
  // ------------------------------------------------------------------
  const [localTransitions, setLocalTransitions] = useState<WorkflowTransition[]>(
    () => transitions.map((t) => ({ ...t })),
  );

  useEffect(() => {
    setLocalTransitions(transitions.map((t) => ({ ...t })));
  }, [transitions]);

  // ------------------------------------------------------------------
  // Collect ALL roles: availableRoles (board-defined) union orphan roles
  // found in any transition.  Orphans are rendered disabled + tooltip.
  // ------------------------------------------------------------------
  const { allRoles, orphanSet } = useMemo(() => {
    const orphans = new Set<string>();
    for (const t of localTransitions) {
      for (const r of normalizeRoles(t.allowed_roles)) {
        if (!availableRoles.includes(r)) orphans.add(r);
      }
    }
    // Board roles first, then orphans (sorted with explicit locale comparator)
    const all = [
      ...availableRoles,
      ...[...orphans].sort((a, b) => a.localeCompare(b)),
    ];
    return { allRoles: all, orphanSet: orphans };
  }, [localTransitions, availableRoles]);

  // ------------------------------------------------------------------
  // Mutation — identical pattern to EdgePropertyPanel.applyTransitionMutation
  // ------------------------------------------------------------------
  const toggleMutation = useMutation({
    mutationFn: async ({
      from,
      to,
      newRoles,
      fieldGates,
    }: {
      from: string;
      to: string;
      newRoles: string[];
      fieldGates: WorkflowTransition["field_gates"];
    }) => {
      if (!workflowId) throw new Error("No workflow selected");
      await api.addTransition(workflowId, from, to, newRoles, fieldGates, boardId);
    },
    // onMutate: snapshot current localTransitions for rollback
    onMutate: async ({ from, to, newRoles }) => {
      const snapshot = localTransitions.map((t) => ({ ...t }));
      // Apply optimistic patch
      setLocalTransitions((prev) =>
        prev.map((t) =>
          t.from === from && t.to === to
            ? { ...t, allowed_roles: newRoles }
            : t,
        ),
      );
      return { snapshot };
    },
    onError: (err: Error, _vars, ctx) => {
      // Rollback
      if (ctx?.snapshot) {
        setLocalTransitions(ctx.snapshot);
      }
      setToast({ variant: "error", message: err.message || "Failed to update permission" });
    },
    onSuccess: () => {
      setToast({ variant: "success", message: "Permission updated" });
    },
    onSettled: () => {
      // Always re-seed from server truth (same as EdgePropertyPanel)
      qc.invalidateQueries({ queryKey: ["workflows", boardKey] });
    },
  });

  // ------------------------------------------------------------------
  // Cell toggle handler
  // ------------------------------------------------------------------
  function handleToggle(transition: WorkflowTransition, role: string) {
    if (readOnly) return;
    if (orphanSet.has(role)) return; // orphan cells are display-only

    const currentRoles = normalizeRoles(transition.allowed_roles);
    const isWildcard = currentRoles.length === 0;

    let newRoles: string[];
    if (isWildcard) {
      // Wildcard → clicking a cell means "restrict to only this role"
      // The first explicit toggle makes this row non-wildcard
      newRoles = [role];
    } else if (currentRoles.includes(role)) {
      newRoles = currentRoles.filter((r) => r !== role);
      // If last role removed → send [] (wildcard / all allowed)
    } else {
      newRoles = [...currentRoles, role];
    }

    toggleMutation.mutate({
      from: transition.from,
      to: transition.to,
      newRoles,
      fieldGates: transition.field_gates,
    });
  }

  // ------------------------------------------------------------------
  // Loading skeleton
  // ------------------------------------------------------------------
  if (isLoading) {
    const skeletonRows = transitions.length > 0 ? transitions.length : 4;
    return (
      <div
        className="overflow-x-auto rounded-md border border-hairline"
        aria-label="Permissions matrix loading"
      >
        <table className="min-w-full table-fixed text-sm" role="grid">
          <thead>
            <tr className="bg-raised">
              <th
                scope="col"
                className="sticky left-0 z-10 min-w-[160px] bg-raised px-4 py-2 text-left font-medium text-text-muted"
              >
                Transition
              </th>
              {(availableRoles.length > 0 ? availableRoles : ["", "", ""]).map(
                (r, i) => (
                  <th
                    key={r || i}
                    scope="col"
                    className="px-3 py-2 font-medium text-text-muted capitalize"
                  >
                    <div className="h-3 animate-pulse rounded bg-raised" />
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: skeletonRows }).map((_, i) => (
              <tr key={i} className="border-t border-hairline">
                <td className="sticky left-0 z-10 bg-surface px-4 py-2">
                  <div className="h-4 w-32 animate-pulse rounded bg-raised" />
                </td>
                {(availableRoles.length > 0 ? availableRoles : ["", "", ""]).map(
                  (r, j) => (
                    <td key={r || j} className="px-3 py-2 text-center">
                      <div className="mx-auto h-4 w-4 animate-pulse rounded bg-raised" />
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Empty state — no workflow selected
  // ------------------------------------------------------------------
  if (!workflowId) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed border-hairline py-8 text-sm text-text-muted"
        data-testid="permission-matrix-empty"
      >
        No workflow selected — pick or create one above
      </div>
    );
  }

  if (localTransitions.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed border-hairline py-8 text-sm text-text-muted"
        data-testid="permission-matrix-no-transitions"
      >
        No transitions defined — add transitions in the Visual Workflow Editor above
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Main matrix
  // ------------------------------------------------------------------
  return (
    <>
      {toast && (
        <Toast
          variant={toast.variant}
          message={toast.message}
          onDismiss={() => setToast(null)}
          durationMs={2000}
        />
      )}

      {/* Read-only banner — same copy as BoardSettings lines 213-224 */}
      {readOnly && (
        <div
          className="mb-3 flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
          role="note"
          data-testid="permission-matrix-readonly-banner"
        >
          <Lock className="h-4 w-4 shrink-0" />
          <span>Read-only — admin or pm role required to edit workflows.</span>
        </div>
      )}

      <div
        className="overflow-x-auto rounded-md border border-hairline"
        data-testid="permission-matrix"
      >
        <table
          className="min-w-full text-sm"
          aria-label="Role permission matrix — rows are transitions, columns are roles"
        >
          {/* Column headers */}
          <thead>
            <tr className="bg-raised">
              {/* First column header — transition label */}
              <th
                scope="col"
                className="sticky left-0 z-10 min-w-[160px] bg-raised px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-text-muted"
              >
                Transition
              </th>

              {allRoles.map((role) => (
                <th
                  key={role}
                  scope="col"
                  className={`px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide ${
                    orphanSet.has(role) ? "text-text-muted opacity-60" : "text-text-muted"
                  }`}
                  title={orphanSet.has(role) ? "Role not defined on this board" : undefined}
                >
                  <span className="block truncate max-w-[80px]">
                    {role.replace(/_/g, " ")}
                  </span>
                  {orphanSet.has(role) && (
                    <span className="block text-[9px] font-normal text-text-muted opacity-70">
                      (orphan)
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {localTransitions.map((transition) => {
              const roles = normalizeRoles(transition.allowed_roles);
              const isWildcard = roles.length === 0;
              const key = rowKey(transition);

              return (
                <tr
                  key={key}
                  className="border-t border-hairline hover:bg-raised/50"
                  aria-description={
                    isWildcard ? "all roles allowed by default" : undefined
                  }
                >
                  {/* First column — "from → to" label + wildcard badge */}
                  <th
                    scope="row"
                    className="sticky left-0 z-10 bg-surface px-4 py-2 text-left"
                  >
                    <div className="flex flex-col gap-0.5">
                      <span className="mono text-xs text-text-secondary whitespace-nowrap">
                        {transition.from}{" "}
                        <span className="text-text-muted" aria-hidden="true">→</span>{" "}
                        {transition.to}
                      </span>
                      {isWildcard && (
                        <span
                          className="inline-block w-fit rounded-sm bg-info-soft px-1.5 py-0.5 text-[10px] font-medium text-info"
                          data-testid={`any-role-badge-${key}`}
                        >
                          Any role
                        </span>
                      )}
                    </div>
                  </th>

                  {/* Role cells */}
                  {allRoles.map((role) => {
                    const isOrphan = orphanSet.has(role);
                    const isChecked = !isWildcard && roles.includes(role);
                    const isPending = toggleMutation.isPending;

                    if (isOrphan) {
                      // Orphan: gray disabled cell, no interaction
                      return (
                        <td
                          key={role}
                          className="px-3 py-2 text-center"
                          title="Role not defined on this board"
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled
                            readOnly
                            aria-label={`${role} for ${transition.from} to ${transition.to} (role not defined on board)`}
                            className="cursor-not-allowed rounded border-hairline accent-[var(--text-muted)] opacity-40"
                          />
                        </td>
                      );
                    }

                    return (
                      <td
                        key={role}
                        className="px-3 py-2 text-center"
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          disabled={readOnly || isPending}
                          onChange={() => handleToggle(transition, role)}
                          aria-label={`${role.replace(/_/g, " ")} for ${transition.from} to ${transition.to}`}
                          data-testid={`matrix-cell-${transition.from}-${transition.to}-${role}`}
                          className={`rounded border-hairline accent-[var(--accent)] ${
                            readOnly
                              ? "cursor-not-allowed opacity-50"
                              : "cursor-pointer"
                          }`}
                        />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Summary caption */}
      <p className="mt-1.5 text-xs text-text-muted">
        {localTransitions.length} transition{localTransitions.length !== 1 ? "s" : ""} &times;{" "}
        {availableRoles.length} role{availableRoles.length !== 1 ? "s" : ""}.
        {" "}Rows with no checkboxes allow all roles (wildcard).
      </p>
    </>
  );
}
