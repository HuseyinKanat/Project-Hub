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
  foldJsonTopLevel,
  formatBytes,
  groupAttachmentsByRun,
  isImage,
  isJsonAttachment,
  isMarkdown,
  isOverCap,
  isSpecKind,
  isTextLike,
  isVideo,
  prettyPrintJson,
  specDocsOfKind,
  TEXT_PREVIEW_CAP_BYTES,
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

// --- PH-300: inline text/JSON preview helpers ---------------------------------

test("isTextLike accepts text/* + application/json MIME", () => {
  assert.equal(isTextLike("text/plain", "notes.md"), true);
  assert.equal(isTextLike("TEXT/PLAIN", "notes"), true); // case-insensitive
  assert.equal(isTextLike("application/json", "x"), true);
  assert.equal(isTextLike("application/vnd.api+json", "x"), true); // +json suffix
});

test("isTextLike sniffs text-family extensions when MIME is octet-stream", () => {
  // Android device logs almost always arrive mislabelled as octet-stream.
  assert.equal(isTextLike("application/octet-stream", "device.logcat"), true);
  assert.equal(isTextLike("application/octet-stream", "run.LOG"), true); // case-insensitive
  assert.equal(isTextLike("application/octet-stream", "notes.txt"), true);
  assert.equal(isTextLike("application/octet-stream", "data.json"), true);
});

test("isTextLike rejects media + unknown binary blobs", () => {
  assert.equal(isTextLike("image/png", "shot.png"), false);
  assert.equal(isTextLike("video/mp4", "clip.mp4"), false);
  assert.equal(isTextLike("application/octet-stream", "dump.bin"), false);
});

test("isJsonAttachment keys off application/json MIME or .json extension", () => {
  assert.equal(isJsonAttachment("application/json", "x"), true);
  assert.equal(isJsonAttachment("application/octet-stream", "report.json"), true);
  assert.equal(isJsonAttachment("application/vnd.api+json", "x"), true);
  assert.equal(isJsonAttachment("text/plain", "run.log"), false);
});

test("prettyPrintJson re-serialises with a 2-space indent", () => {
  assert.equal(prettyPrintJson('{"a":1,"b":{"c":2}}'), '{\n  "a": 1,\n  "b": {\n    "c": 2\n  }\n}');
});

test("prettyPrintJson throws on invalid JSON (caller falls back to raw)", () => {
  assert.throws(() => prettyPrintJson("not json{"));
});

test("foldJsonTopLevel splits an object root into foldable top-level entries", () => {
  const entries = foldJsonTopLevel('{"a":1,"b":{"c":2},"list":[1,2,3],"s":"hi"}');
  assert.ok(entries);
  assert.deepEqual(
    entries.map((e) => e.key),
    ["a", "b", "list", "s"],
  );
  // scalars: summary is the JSON literal, body null (nothing to expand)
  assert.deepEqual(entries[0], { key: "a", summary: "1", body: null });
  assert.deepEqual(entries[3], { key: "s", summary: '"hi"', body: null });
  // object value: item-count summary + pretty 2-space body
  assert.equal(entries[1].summary, "{ 1 anahtar }");
  assert.equal(entries[1].body, '{\n  "c": 2\n}');
  // array value: öğe-count summary + pretty body
  assert.equal(entries[2].summary, "[ 3 öğe ]");
  assert.equal(entries[2].body, "[\n  1,\n  2,\n  3\n]");
});

test("foldJsonTopLevel returns null for array / scalar / invalid roots", () => {
  assert.equal(foldJsonTopLevel("[1,2,3]"), null); // array root → flat pretty
  assert.equal(foldJsonTopLevel('"just a string"'), null); // scalar root
  assert.equal(foldJsonTopLevel("42"), null);
  assert.equal(foldJsonTopLevel("null"), null);
  assert.equal(foldJsonTopLevel("not json{"), null); // parse failure
});

test("TEXT_PREVIEW_CAP_BYTES is 512 KiB", () => {
  assert.equal(TEXT_PREVIEW_CAP_BYTES, 512 * 1024);
});

test("isOverCap is true only STRICTLY above the 512-KiB cap", () => {
  // The chip/row popup gate keys off this; DocPopup would download the whole blob,
  // so an over-cap doc must offer download instead of opening the popup.
  assert.equal(isOverCap({ size_bytes: 0 }), false);
  assert.equal(isOverCap({ size_bytes: TEXT_PREVIEW_CAP_BYTES - 1 }), false);
  assert.equal(isOverCap({ size_bytes: TEXT_PREVIEW_CAP_BYTES }), false); // == cap → NOT over (strict >)
  assert.equal(isOverCap({ size_bytes: TEXT_PREVIEW_CAP_BYTES + 1 }), true);
});

// --- PH-310: markdown routing + spec-doc chip filtering -----------------------

test("isMarkdown accepts text/markdown MIME + .md/.markdown extensions", () => {
  assert.equal(isMarkdown("text/markdown", "spec.md"), true);
  assert.equal(isMarkdown("TEXT/MARKDOWN", "spec"), true); // case-insensitive
  assert.equal(isMarkdown("text/x-markdown", "spec"), true);
  assert.equal(isMarkdown("text/plain", "notes.md"), true); // extension wins
  assert.equal(isMarkdown("application/octet-stream", "README.MARKDOWN"), true);
});

test("isMarkdown rejects non-markdown text/media blobs", () => {
  assert.equal(isMarkdown("text/plain", "run.log"), false);
  assert.equal(isMarkdown("application/json", "report.json"), false);
  assert.equal(isMarkdown("image/png", "shot.png"), false);
  assert.equal(isMarkdown("application/octet-stream", "dump.bin"), false);
});

test("isMarkdown precedes isTextLike for a .md doc (routing order)", () => {
  // A `.md` upload lands as text/markdown → BOTH would return true; the consumer
  // checks isMarkdown FIRST so it renders as prose, not a mono <pre>.
  assert.equal(isMarkdown("text/markdown", "AC.md"), true);
  assert.equal(isTextLike("text/markdown", "AC.md"), true);
});

test("isSpecKind is true only for usecase / testcase", () => {
  assert.equal(isSpecKind("usecase"), true);
  assert.equal(isSpecKind("testcase"), true);
  assert.equal(isSpecKind("screenshot"), false);
  assert.equal(isSpecKind("report"), false);
  assert.equal(isSpecKind("other"), false);
  assert.equal(isSpecKind(""), false);
});

test("specDocsOfKind filters by kind, oldest→newest, filename tiebreak", () => {
  const items = [
    mk({ kind: "testcase", filename: "tc.md", created_at: "2026-07-13T10:00:00Z" }),
    mk({ kind: "usecase", filename: "02_b.md", created_at: "2026-07-13T10:02:00Z" }),
    mk({ kind: "usecase", filename: "01_a.md", created_at: "2026-07-13T10:01:00Z" }),
    mk({ kind: "screenshot", filename: "shot.png", created_at: "2026-07-13T10:00:00Z" }),
    mk({ kind: "usecase", filename: "a.md", created_at: "2026-07-13T10:01:00Z" }), // tie w/ 01_a
  ];
  const useCases = specDocsOfKind(items, "usecase");
  assert.deepEqual(
    useCases.map((a) => a.filename),
    ["01_a.md", "a.md", "02_b.md"], // 10:01 tie → filename, then 10:02
  );
  assert.equal(specDocsOfKind(items, "testcase").length, 1);
  assert.deepEqual(specDocsOfKind([], "usecase"), []);
});
