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
