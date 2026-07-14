/**
 * grouping.ts — PH-297
 *
 * Pure, side-effect-free helpers for the evidence Attachments card. Extracted so
 * the ordering/grouping contract is unit-testable (node:test) independent of React
 * — the same colocated-`.test.ts` pattern the repo already uses (identityGuard,
 * branchGraphLayout).
 *
 * Contract:
 *   groupAttachmentsByRun — partitions attachments by `run_id`. NAMED runs come
 *   first, most-recently-active on top ("en yeni üstte", keyed off each group's
 *   newest item). The ungrouped bucket (`run_id === null`) is ALWAYS last and
 *   labelled "Diğer". WITHIN a run, items are oldest → newest (a test run's step
 *   screenshots read top-to-bottom), filename as a deterministic tiebreak.
 *
 *   formatBytes — binary (1024) human size; the backend cap is 25 MiB so KiB/MiB
 *   dominate. Sub-1-KiB shows raw bytes; larger rounds to one decimal.
 */

import type { AttachmentResponse } from "@/types/api";

export const UNGROUPED_LABEL = "Diğer";

export interface AttachmentGroup {
  /** null → the ungrouped ("Diğer") bucket. */
  runId: string | null;
  label: string;
  items: AttachmentResponse[];
}

function tsOf(iso: string): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

function newestTs(items: AttachmentResponse[]): number {
  return items.reduce((max, a) => Math.max(max, tsOf(a.created_at)), 0);
}

/** Group attachments by run_id; named runs newest-first, "Diğer" bucket last. */
export function groupAttachmentsByRun(
  items: AttachmentResponse[],
): AttachmentGroup[] {
  const byRun = new Map<string | null, AttachmentResponse[]>();
  for (const a of items) {
    const key = a.run_id ?? null;
    const bucket = byRun.get(key);
    if (bucket) bucket.push(a);
    else byRun.set(key, [a]);
  }

  const groups: AttachmentGroup[] = [];
  for (const [runId, bucket] of byRun) {
    const sorted = [...bucket].sort((x, y) => {
      const dt = tsOf(x.created_at) - tsOf(y.created_at);
      if (dt !== 0) return dt; // oldest → newest within a run (step order)
      return x.filename.localeCompare(y.filename);
    });
    groups.push({ runId, label: runId ?? UNGROUPED_LABEL, items: sorted });
  }

  // Named runs first (newest activity on top); the null/"Diğer" group always last.
  groups.sort((a, b) => {
    if (a.runId === null) return 1;
    if (b.runId === null) return -1;
    return newestTs(b.items) - newestTs(a.items);
  });
  return groups;
}

const SIZE_UNITS = ["KiB", "MiB", "GiB", "TiB"] as const;

/** Human-readable binary file size (e.g. 1536 → "1.5 KiB", 26214400 → "25 MiB"). */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  let value = bytes / 1024;
  let idx = 0;
  while (value >= 1024 && idx < SIZE_UNITS.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const rounded = Math.round(value * 10) / 10;
  return `${rounded} ${SIZE_UNITS[idx] ?? "KiB"}`;
}

/** True when the blob can render inline as an image thumbnail/lightbox. */
export function isImage(contentType: string): boolean {
  return contentType.toLowerCase().startsWith("image/");
}

/** True when the blob can render in a native <video> player (mp4 seek via Range). */
export function isVideo(contentType: string): boolean {
  return contentType.toLowerCase().startsWith("video/");
}

// ---------------------------------------------------------------------------
// PH-300 — inline TEXT/JSON preview helpers (pure, node:test-covered).
//
// The evidence card can preview text-family blobs inline (JSON pretty-print with
// top-level fold; .log/.txt monospace). Detection is deliberately generous on the
// extension side: device logs (`.logcat`, `.log`) very often arrive as
// `application/octet-stream`, so a MIME check alone would miss them.
// ---------------------------------------------------------------------------

/** Default inline-preview byte cap. Above this we skip the fetch and offer download. */
export const TEXT_PREVIEW_CAP_BYTES = 512 * 1024; // 512 KiB

const TEXT_EXTENSIONS = [".json", ".log", ".txt", ".logcat"] as const;

/**
 * True when a blob is worth previewing as text — `text/*` and `application/json`
 * MIME, OR a text-family filename extension (covers `.logcat`/`.log`/`.txt`/`.json`
 * mislabelled `application/octet-stream`, the common Android device-log case).
 */
export function isTextLike(contentType: string, filename: string): boolean {
  const ct = (contentType ?? "").toLowerCase();
  if (ct.startsWith("text/")) return true;
  if (ct === "application/json" || ct.endsWith("+json")) return true;
  const name = (filename ?? "").toLowerCase();
  return TEXT_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/** True when a text-like blob should render through the JSON (pretty + fold) path. */
export function isJsonAttachment(contentType: string, filename: string): boolean {
  const ct = (contentType ?? "").toLowerCase();
  if (ct === "application/json" || ct.endsWith("+json")) return true;
  return (filename ?? "").toLowerCase().endsWith(".json");
}

/** Parse + re-serialise with a 2-space indent. Throws if `raw` is not valid JSON. */
export function prettyPrintJson(raw: string): string {
  return JSON.stringify(JSON.parse(raw), null, 2);
}

/** One foldable top-level entry of a JSON object. */
export interface JsonFoldEntry {
  key: string;
  /** Collapsed one-line summary — a scalar literal, or an object/array item count. */
  summary: string;
  /** 2-space pretty body shown when expanded; null for scalars (summary is the value). */
  body: string | null;
}

/**
 * Split a JSON OBJECT into foldable top-level entries — a NARROW fold (each entry's
 * body stays fully expanded; there is no recursive tree). Returns null when `raw` is
 * invalid JSON or the root is not a plain object (array/scalar roots render flat via
 * {@link prettyPrintJson}).
 */
export function foldJsonTopLevel(raw: string): JsonFoldEntry[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  return Object.entries(parsed as Record<string, unknown>).map(([key, value]) => {
    if (value !== null && typeof value === "object") {
      const count = Array.isArray(value)
        ? value.length
        : Object.keys(value as object).length;
      const summary = Array.isArray(value)
        ? `[ ${count} öğe ]`
        : `{ ${count} anahtar }`;
      return { key, summary, body: JSON.stringify(value, null, 2) };
    }
    return { key, summary: JSON.stringify(value), body: null };
  });
}
