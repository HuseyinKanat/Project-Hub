/**
 * permissions.ts — PH-297
 *
 * Pure, side-effect-free evidence-upload permission check, extracted so the
 * NESTED board.roles contract is unit-testable (node:test) independent of React
 * — the repo's colocated `.test.ts` convention (grouping, identityGuard,
 * branchGraphLayout).
 *
 * WHY a helper: the board role→capability map arrives NESTED on the wire
 * (`board.roles.roles[role].permissions`), NOT flat (`board.roles[role]`). The
 * old inline check in `TicketDetail` read the flat path, so `canUpload` was
 * ALWAYS false and the upload form never rendered (qa_failed iter-1). The backend
 * gates the SAME nested path (core/permissions.py `role_permissions`), where a
 * `"*"` grant matches any capability (`_permission_matches`). Centralising the
 * rule here kills the copy-paste that let `TicketDetail` drift from the
 * nested-aware `BoardSettings` / `MembersTab` consumers.
 */

import type { BoardResponse } from "@/types/api";

/** The capability the backend gates evidence upload on (core/permissions.py). */
export const ATTACHMENT_ADD_CAP = "attachment.add";
/** PH-313 capability: edit an attachment's metadata (phase/kind/run_id). */
export const ATTACHMENT_UPDATE_CAP = "attachment.update";
/** PH-313 capability: hard-delete an attachment (restricted to pm + qa). */
export const ATTACHMENT_DELETE_CAP = "attachment.delete";

/**
 * Shared nested-path capability check — the SINGLE place that reads the
 * authoritative `board.roles.roles[role].permissions` wire shape (PH-297). All
 * three public helpers below delegate here so the nested-vs-flat contract (the
 * qa_failed iter-1 gotcha) is enforced once:
 *   - `admin` ALWAYS passes (UI fast-path; its grant is the `["*"]` wildcard);
 *   - otherwise the role's NESTED permission list must include `cap` OR the `"*"`
 *     wildcard — mirroring backend `_permission_matches` 1:1.
 * Fails CLOSED: null/absent board, null/absent role, or a role missing from the
 * nested map → `false`. The server's 403 is the authoritative backstop.
 */
function hasCap(
  board: Pick<BoardResponse, "roles"> | null | undefined,
  role: string | null | undefined,
  cap: string,
): boolean {
  if (!role) return false;
  if (role === "admin") return true;
  const permissions = board?.roles?.roles?.[role]?.permissions;
  if (!permissions) return false;
  return permissions.includes(cap) || permissions.includes("*");
}

/** True when `role` may UPLOAD evidence to a ticket on `board` (`attachment.add`). */
export function canUploadAttachment(
  board: Pick<BoardResponse, "roles"> | null | undefined,
  role: string | null | undefined,
): boolean {
  return hasCap(board, role, ATTACHMENT_ADD_CAP);
}

/** True when `role` may EDIT an attachment's phase/metadata (`attachment.update`). */
export function canUpdateAttachment(
  board: Pick<BoardResponse, "roles"> | null | undefined,
  role: string | null | undefined,
): boolean {
  return hasCap(board, role, ATTACHMENT_UPDATE_CAP);
}

/** True when `role` may DELETE an attachment (`attachment.delete`; pm/qa server-side). */
export function canDeleteAttachment(
  board: Pick<BoardResponse, "roles"> | null | undefined,
  role: string | null | undefined,
): boolean {
  return hasCap(board, role, ATTACHMENT_DELETE_CAP);
}
