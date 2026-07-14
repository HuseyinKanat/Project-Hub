/**
 * permissions.test.ts — PH-297 (qa_failed iter-1 regression lock)
 *
 * Locks `canUploadAttachment` against the REAL nested board.roles wire shape
 * (`board.roles.roles[role].permissions`) the backend gates on. The bug: the old
 * inline check read the FLAT path (`board.roles[role]`), so `canUpload` was
 * always false and the evidence upload form never rendered. Runs via Node's
 * built-in test runner (node:test + native TS strip), NOT the app tsc — the repo
 * convention (see grouping.test.ts, identityGuard.test.ts). Run:
 *   node --test --experimental-strip-types src/components/attachments/permissions.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { ATTACHMENT_ADD_CAP, canUploadAttachment } from "./permissions.ts";
import type { BoardResponse } from "@/types/api";

/**
 * Build a board carrying ONLY the nested role map the helper reads. Note the
 * DOUBLE nesting: `board.roles.roles[role]` — the outer `roles` is the wire
 * field, the inner `roles` is the authoritative role→permissions map.
 */
function board(
  roleMap: Record<string, { permissions: string[] }>,
): Pick<BoardResponse, "roles"> {
  return { roles: { roles: roleMap } };
}

test("nested real shape: a role whose permissions include attachment.add may upload", () => {
  const b = board({ qa: { permissions: ["ticket.read", ATTACHMENT_ADD_CAP] } });
  assert.equal(canUploadAttachment(b, "qa"), true);
});

test("admin bypasses via fast-path — even with no explicit map entry", () => {
  assert.equal(canUploadAttachment(board({}), "admin"), true);
});

test("a '*' wildcard grant may upload (mirrors backend _permission_matches)", () => {
  const b = board({ pm: { permissions: ["*"] } });
  assert.equal(canUploadAttachment(b, "pm"), true);
});

test("a role WITHOUT the cap may NOT upload", () => {
  const b = board({ reviewer: { permissions: ["ticket.read", "comment.add"] } });
  assert.equal(canUploadAttachment(b, "reviewer"), false);
});

test("a role absent from the nested map may NOT upload", () => {
  const b = board({ qa: { permissions: [ATTACHMENT_ADD_CAP] } });
  assert.equal(canUploadAttachment(b, "frontend_dev"), false);
});

test("null / undefined role or board fails CLOSED", () => {
  const b = board({ qa: { permissions: [ATTACHMENT_ADD_CAP] } });
  assert.equal(canUploadAttachment(b, null), false);
  assert.equal(canUploadAttachment(b, undefined), false);
  assert.equal(canUploadAttachment(null, "qa"), false);
  assert.equal(canUploadAttachment(undefined, "qa"), false);
});

test("REGRESSION (qa_failed iter-1): reads the NESTED path, not the flat one", () => {
  // Reproduce what the bug relied on: a FLAT board with the role at the TOP
  // level (`board.roles[role]`, no inner `.roles`). The correct nested reader
  // must return false — proving it does NOT silently fall back to the flat path
  // the old inline check used.
  const flatShaped = {
    roles: { qa: { permissions: [ATTACHMENT_ADD_CAP] } },
  } as unknown as Pick<BoardResponse, "roles">;
  assert.equal(canUploadAttachment(flatShaped, "qa"), false);
});
