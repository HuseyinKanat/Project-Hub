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

/**
 * True when a blob is over the inline-preview cap ({@link TEXT_PREVIEW_CAP_BYTES}).
 * EVERY DocPopup caller (AttachmentItem row + TicketDetail SpecDocChips) MUST gate on
 * this FIRST and offer download instead of opening the popup: `api.fetchAttachmentText`
 * downloads the WHOLE blob (its `maxBytes` only slices the already-fetched text for
 * DISPLAY), so this metadata check is the only thing that actually prevents pulling a
 * huge file. Strict `>` — a blob exactly at the cap still previews.
 */
export function isOverCap(
  attachment: Pick<AttachmentResponse, "size_bytes">,
): boolean {
  return attachment.size_bytes > TEXT_PREVIEW_CAP_BYTES;
}

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

// ---------------------------------------------------------------------------
// PH-310 — SpecDoc experience: markdown routing + AC/Test-Plan chip filtering.
//
// A `.md` attachment (PH-309 allowed `text/markdown` uploads) should open as
// RENDERED prose, not a monospace <pre>. `isMarkdown` is the narrow gate the
// DocPopup/AttachmentItem check BEFORE `isTextLike` — a markdown doc is text-like
// too, so ordering (markdown → json → text) is what routes it correctly.
// ---------------------------------------------------------------------------

const MARKDOWN_EXTENSIONS = [".md", ".markdown"] as const;

/**
 * True when a blob should render through the MARKDOWN path — `text/markdown`
 * (or `text/x-markdown`) MIME OR a `.md`/`.markdown` filename extension (covers a
 * `.md` mislabelled `application/octet-stream`). MUST be checked BEFORE
 * {@link isTextLike}: markdown is text-like, so first-match ordering is what sends
 * it to prose rendering instead of a mono `<pre>`.
 */
export function isMarkdown(contentType: string, filename: string): boolean {
  const ct = (contentType ?? "").toLowerCase();
  if (ct === "text/markdown" || ct === "text/x-markdown") return true;
  const name = (filename ?? "").toLowerCase();
  return MARKDOWN_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/** The two spec-doc attachment kinds surfaced as field chips (not evidence rows). */
export type SpecKind = "usecase" | "testcase";

/**
 * True for the spec-doc kinds (`usecase` → AC, `testcase` → Test Plan). These are
 * FILTERED OUT of the "Kanıtlar" evidence card and instead rendered as chips under
 * their owning field on the ticket detail page.
 */
export function isSpecKind(kind: string): kind is SpecKind {
  return kind === "usecase" || kind === "testcase";
}

/**
 * Spec-doc attachments of ONE kind, deterministically ordered (oldest → newest by
 * `created_at`, filename as a stable tiebreak) for a fixed chip row. Pure —
 * node:test-covered; the TicketDetail chips read straight from this.
 */
export function specDocsOfKind(
  items: AttachmentResponse[],
  kind: SpecKind,
): AttachmentResponse[] {
  return items
    .filter((a) => a.kind === kind)
    .sort((x, y) => {
      const dt = tsOf(x.created_at) - tsOf(y.created_at);
      if (dt !== 0) return dt;
      return x.filename.localeCompare(y.filename);
    });
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

// ---------------------------------------------------------------------------
// PH-312 — evidence STORY view: phase-grouped chronology + iteration counter.
//
// When attachments carry a `phase` slug (PH-311 backend), the evidence card reads
// as a fix NARRATIVE — Reproduce → iterations → resolution — instead of PH-297's
// run_id buckets. All ordering is a PURE function of the slug (a deterministic
// semantic "story rank"), so the chronology holds regardless of upload time
// (a repro uploaded after an iteration still sorts first). When NO item carries a
// phase, AttachmentsSection falls back to `groupAttachmentsByRun` VERBATIM.
//
// Rank tuple [category, iterN, subOrder] (lexicographic):
//   repro        → [0,0,0]   before      → [0,0,1]
//   iter-N-fail  → [1,N,0]   iter-N-pass → [1,N,1]
//   after        → [2,0,0]
//   unknown slug → [3,0,0]   (valid but non-conventional, e.g. "smoke-check")
//   phaseless    → [4,0,0]   ("Diğer", always last)
// Equal tuples (multiple unknown slugs) tiebreak on min(created_at) then slug.
// ---------------------------------------------------------------------------

/** `iter-<N>-fail` / `iter-<N>-pass` slug shape (N unbounded). */
const ITER_PHASE_RE = /^iter-(\d+)-(fail|pass)$/;

/** Fixed human titles for the conventional non-iteration phases. */
export const PHASE_TITLES: Readonly<Record<string, string>> = {
  repro: "Reproduce",
  before: "Öncesi",
  after: "Sonrası",
};

export interface PhaseGroup {
  /** Phase slug; null → the phaseless ("Diğer") bucket. */
  slug: string | null;
  /** Human-readable heading (iteration slugs expand to "İterasyon N — …"). */
  label: string;
  items: AttachmentResponse[];
}

/**
 * Normalise a raw `phase` to a meaningful slug or null: trims whitespace and maps
 * empty → null, so a blank phase drops into the phaseless bucket instead of forming
 * an empty-labelled group.
 */
function normPhase(phase: string | null | undefined): string | null {
  if (phase == null) return null;
  const trimmed = phase.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** True when at least one attachment carries a (non-empty) phase — drives the story-vs-run branch. */
export function hasAnyPhase(items: AttachmentResponse[]): boolean {
  return items.some((a) => normPhase(a.phase) !== null);
}

/** Deterministic story-rank tuple for a phase slug (null = phaseless). */
export function phaseRank(slug: string | null): [number, number, number] {
  if (slug === null) return [4, 0, 0];
  if (slug === "repro") return [0, 0, 0];
  if (slug === "before") return [0, 0, 1];
  if (slug === "after") return [2, 0, 0];
  const m = ITER_PHASE_RE.exec(slug);
  if (m) return [1, Number.parseInt(m[1] ?? "0", 10), m[2] === "pass" ? 1 : 0];
  return [3, 0, 0]; // valid but unknown slug
}

/** Human heading for a phase slug (raw slug for unknowns, "Diğer" for phaseless). */
export function phaseTitle(slug: string | null): string {
  if (slug === null) return UNGROUPED_LABEL;
  const fixed = PHASE_TITLES[slug];
  if (fixed) return fixed;
  const m = ITER_PHASE_RE.exec(slug);
  if (m) {
    const n = m[1] ?? "0";
    return m[2] === "pass" ? `İterasyon ${n} — çözüldü` : `İterasyon ${n} — tutmadı`;
  }
  return slug; // unknown slug → raw label
}

function minCreatedTs(items: AttachmentResponse[]): number {
  return items.reduce(
    (min, a) => Math.min(min, tsOf(a.created_at)),
    Number.POSITIVE_INFINITY,
  );
}

/**
 * Partition attachments into story groups ordered by semantic phase rank. Within a
 * phase, items read oldest → newest (filename tiebreak), like a run's step shots.
 * INVARIANT: every input item lands in exactly one group (no drops) — the phaseless
 * bucket collects any item without a phase, last.
 */
export function groupAttachmentsByPhase(
  items: AttachmentResponse[],
): PhaseGroup[] {
  const bySlug = new Map<string | null, AttachmentResponse[]>();
  for (const a of items) {
    const slug = normPhase(a.phase);
    const bucket = bySlug.get(slug);
    if (bucket) bucket.push(a);
    else bySlug.set(slug, [a]);
  }

  const groups: PhaseGroup[] = [];
  for (const [slug, bucket] of bySlug) {
    const sorted = [...bucket].sort((x, y) => {
      const dt = tsOf(x.created_at) - tsOf(y.created_at);
      if (dt !== 0) return dt; // oldest → newest within a phase (step order)
      return x.filename.localeCompare(y.filename);
    });
    groups.push({ slug, label: phaseTitle(slug), items: sorted });
  }

  groups.sort((a, b) => {
    const [ca, na, sa] = phaseRank(a.slug);
    const [cb, nb, sb] = phaseRank(b.slug);
    if (ca !== cb) return ca - cb;
    if (na !== nb) return na - nb;
    if (sa !== sb) return sa - sb;
    // equal rank (e.g. multiple unknown slugs) → chronology, then slug.
    const dt = minCreatedTs(a.items) - minCreatedTs(b.items);
    if (dt !== 0) return dt;
    return (a.slug ?? "").localeCompare(b.slug ?? "");
  });
  return groups;
}

/**
 * One-line iteration summary for the story header:
 *   • reached an `iter-N-pass` → "N iterasyonda çözüldü" (N = highest passing iter)
 *   • only `iter-N-fail`s      → "N iterasyon — henüz çözülmedi"
 *   • no iteration phase       → null (no summary line)
 */
export function summarizeIterations(items: AttachmentResponse[]): string | null {
  let maxPassN: number | null = null;
  let maxFailN: number | null = null;
  for (const a of items) {
    const m = ITER_PHASE_RE.exec(normPhase(a.phase) ?? "");
    if (!m) continue;
    const n = Number.parseInt(m[1] ?? "0", 10);
    if (m[2] === "pass") maxPassN = Math.max(maxPassN ?? n, n);
    else maxFailN = Math.max(maxFailN ?? n, n);
  }
  if (maxPassN !== null) return `${maxPassN} iterasyonda çözüldü`;
  if (maxFailN !== null) return `${maxFailN} iterasyon — henüz çözülmedi`;
  return null;
}

/** Before/after comparison anchors — a null pair unless BOTH ends exist. */
export interface BeforeAfterAnchors {
  before: PhaseGroup | null;
  after: PhaseGroup | null;
}

/**
 * Pick the before/after comparison anchors from already-grouped phases:
 *   before = the `before` group, else `repro`
 *   after  = the `after` group,  else the highest-N `iter-N-pass`
 * If EITHER end is missing, returns a null pair (no comparison is shown — AC3).
 */
export function beforeAfterAnchors(groups: PhaseGroup[]): BeforeAfterAnchors {
  const bySlug = new Map<string, PhaseGroup>();
  let maxPassGroup: PhaseGroup | null = null;
  let maxPassN = -1;
  for (const g of groups) {
    if (g.slug !== null) bySlug.set(g.slug, g);
    const m = g.slug ? ITER_PHASE_RE.exec(g.slug) : null;
    if (m && m[2] === "pass") {
      const n = Number.parseInt(m[1] ?? "0", 10);
      if (n > maxPassN) {
        maxPassN = n;
        maxPassGroup = g;
      }
    }
  }
  const before = bySlug.get("before") ?? bySlug.get("repro") ?? null;
  const after = bySlug.get("after") ?? maxPassGroup ?? null;
  if (before === null || after === null) return { before: null, after: null };
  return { before, after };
}

// ---------------------------------------------------------------------------
// PH-314 — phase-slug COMPOSITION for the upload/edit selector (pure, node:test).
//
// The evidence upload form (and the row phase-editor) offers a phase base
// (`repro | iter | before | after | ""`) plus, for iterations, an N counter and a
// fail/pass outcome. These two pure helpers turn that UI state into the canonical
// slug string the backend stores (PH-311) and that `phaseRank`/`phaseTitle` above
// already understand — keeping the wire slug a deterministic function of the form
// so the composition is unit-testable independent of React.
// ---------------------------------------------------------------------------

/** The non-numeric phase bases the selector offers (`""` → phaseless). */
export type PhaseBase = "" | "repro" | "iter" | "before" | "after";

/**
 * Suggest the DEFAULT iteration number when the user picks an `iter-*` phase:
 * the highest iteration observed across existing `iter-N-fail`/`iter-N-pass`
 * evidence + 1, so the next fix attempt auto-numbers. With NO iteration evidence
 * (empty list, or only repro/before/after) → 1. Pure — reuses `ITER_PHASE_RE`.
 */
export function suggestNextIter(items: AttachmentResponse[]): number {
  let maxN = 0;
  for (const a of items) {
    const m = ITER_PHASE_RE.exec(normPhase(a.phase) ?? "");
    if (m) maxN = Math.max(maxN, Number.parseInt(m[1] ?? "0", 10));
  }
  return maxN + 1;
}

/**
 * Compose the canonical phase slug from the selector state:
 *   - `""`               → null (phaseless — the attachment carries no phase)
 *   - `repro`/`before`/`after` → the literal slug (iterN/outcome ignored)
 *   - `iter`             → `iter-<iterN>-<fail|pass>`
 * `iterN` is only consulted for the `iter` base; callers guarantee it is a
 * positive integer (the number input is `min=1`, prefilled by `suggestNextIter`).
 * An unknown base returns null (defensive — the selector only emits the five above).
 */
export function composePhaseSlug(
  base: string,
  iterN: number,
  outcome: "fail" | "pass",
): string | null {
  if (base === "iter") return `iter-${iterN}-${outcome}`;
  if (base === "repro" || base === "before" || base === "after") return base;
  return null; // "" or any unexpected base → phaseless
}

/**
 * The flat `<select>` value the upload/edit phase picker uses — the two iteration
 * outcomes are SEPARATE options (`iter-fail`/`iter-pass`) so the whole phase is one
 * dropdown choice; the numeric N rides in a sibling input. `""` → phaseless.
 */
export type PhaseSelector =
  | ""
  | "repro"
  | "iter-fail"
  | "iter-pass"
  | "before"
  | "after";

/** Map a picker value (+N) to the canonical slug — a thin {@link composePhaseSlug} adapter. */
export function selectorToSlug(sel: PhaseSelector, iterN: number): string | null {
  if (sel === "iter-fail") return composePhaseSlug("iter", iterN, "fail");
  if (sel === "iter-pass") return composePhaseSlug("iter", iterN, "pass");
  return composePhaseSlug(sel, iterN, "fail"); // "", repro, before, after (N/outcome ignored)
}

/**
 * Inverse of {@link selectorToSlug}: seed the picker (+N) from an EXISTING slug, for
 * the row "edit phase" affordance. An unknown/non-conventional slug can't be
 * represented by the fixed picker, so it seeds to phaseless (`""`, N=1) — the editor
 * is opt-in and cancel sends no request, so this is only lossy on an explicit save.
 */
export function slugToSelector(slug: string | null): {
  sel: PhaseSelector;
  iterN: number;
} {
  if (slug === null) return { sel: "", iterN: 1 };
  if (slug === "repro" || slug === "before" || slug === "after") {
    return { sel: slug, iterN: 1 };
  }
  const m = ITER_PHASE_RE.exec(slug);
  if (m) {
    return {
      sel: m[2] === "pass" ? "iter-pass" : "iter-fail",
      iterN: Number.parseInt(m[1] ?? "1", 10),
    };
  }
  return { sel: "", iterN: 1 }; // unknown slug → phaseless in the picker
}
