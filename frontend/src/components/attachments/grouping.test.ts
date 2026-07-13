/**
 * grouping.test.ts — PH-297
 *
 * Locks the pure ordering/grouping + size-format contract of the evidence
 * Attachments card. Runs via Node's built-in test runner (node:test + native TS
 * strip), NOT the app tsc — the repo convention (see identityGuard.test.ts,
 * branchGraphLayout.test.ts). Run:
 *   node --test --experimental-strip-types src/components/attachments/grouping.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatBytes,
  groupAttachmentsByRun,
  isImage,
  isVideo,
  UNGROUPED_LABEL,
} from "./grouping.ts";
import type { AttachmentResponse } from "@/types/api";

let seq = 0;
function mk(
  partial: Partial<AttachmentResponse> & Pick<AttachmentResponse, "created_at">,
): AttachmentResponse {
  seq += 1;
  return {
    id: partial.id ?? `id-${seq}`,
    ticket_id: "t-1",
    filename: partial.filename ?? `f-${seq}.png`,
    content_type: partial.content_type ?? "image/png",
    size_bytes: partial.size_bytes ?? 100,
    checksum_sha256: "deadbeef",
    kind: partial.kind ?? "screenshot",
    source: partial.source ?? "human",
    run_id: partial.run_id ?? null,
    author: {
      id: "a-1",
      kind: "agent",
      display_name: "jarwis-qa",
      agent_id: null,
      agent_role_hint: "qa",
    },
    created_at: partial.created_at,
  };
}

test("groups by run_id; named runs newest-first, 'Diğer' bucket last", () => {
  const items = [
    mk({ run_id: "run-A", created_at: "2026-07-13T10:00:00Z" }),
    mk({ run_id: null, created_at: "2026-07-13T12:00:00Z" }), // newest overall, but ungrouped
    mk({ run_id: "run-B", created_at: "2026-07-13T11:00:00Z" }),
  ];
  const groups = groupAttachmentsByRun(items);
  assert.equal(groups.length, 3);
  // run-B (11:00) newer than run-A (10:00) → B first; null bucket always last
  assert.deepEqual(
    groups.map((g) => g.runId),
    ["run-B", "run-A", null],
  );
  assert.equal(groups[2].label, UNGROUPED_LABEL);
});

test("within a run, items are oldest → newest (step order)", () => {
  const items = [
    mk({ run_id: "r", filename: "03_c.png", created_at: "2026-07-13T10:03:00Z" }),
    mk({ run_id: "r", filename: "01_a.png", created_at: "2026-07-13T10:01:00Z" }),
    mk({ run_id: "r", filename: "02_b.png", created_at: "2026-07-13T10:02:00Z" }),
  ];
  const [group] = groupAttachmentsByRun(items);
  assert.deepEqual(
    group.items.map((a) => a.filename),
    ["01_a.png", "02_b.png", "03_c.png"],
  );
});

test("equal timestamps fall back to filename for a deterministic order", () => {
  const t = "2026-07-13T10:00:00Z";
  const items = [
    mk({ run_id: "r", filename: "b.png", created_at: t }),
    mk({ run_id: "r", filename: "a.png", created_at: t }),
  ];
  const [group] = groupAttachmentsByRun(items);
  assert.deepEqual(
    group.items.map((a) => a.filename),
    ["a.png", "b.png"],
  );
});

test("empty input → no groups", () => {
  assert.deepEqual(groupAttachmentsByRun([]), []);
});

test("formatBytes renders binary units", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1024), "1 KiB");
  assert.equal(formatBytes(1536), "1.5 KiB");
  assert.equal(formatBytes(26_214_400), "25 MiB"); // the backend 25-MiB cap
  assert.equal(formatBytes(-1), "—");
  assert.equal(formatBytes(Number.NaN), "—");
});

test("isImage / isVideo key off the content-type prefix (case-insensitive)", () => {
  assert.equal(isImage("image/png"), true);
  assert.equal(isImage("IMAGE/JPEG"), true);
  assert.equal(isImage("video/mp4"), false);
  assert.equal(isVideo("video/mp4"), true);
  assert.equal(isVideo("application/json"), false);
});
