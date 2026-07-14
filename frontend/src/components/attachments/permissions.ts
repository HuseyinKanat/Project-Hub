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

/**
 * True when `role` may upload evidence to a ticket on `board`:
 *   - `admin` ALWAYS may (UI fast-path; its grant is the `["*"]` wildcard anyway);
 *   - otherwise the role's NESTED permission list (`board.roles.roles[role]`)
 *     must include `attachment.add` OR the `"*"` wildcard — mirroring the
 *     backend `_permission_matches` semantics 1:1.
 *
 * Fails CLOSED: a null/absent board, a null/absent role, or a role missing from
 * the nested map → `false`. The server's 403 is the authoritative backstop if
 * the client-side view and the server ever disagree.
 */
export function canUploadAttachment(
  board: Pick<BoardResponse, "roles"> | null | undefined,
  role: string | null | undefined,
): boolean {
  if (!role) return false;
  if (role === "admin") return true;
  const permissions = board?.roles?.roles?.[role]?.permissions;
  if (!permissions) return false;
  return permissions.includes(ATTACHMENT_ADD_CAP) || permissions.includes("*");
}
