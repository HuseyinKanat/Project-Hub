/**
 * parseDiff.ts — custom unified diff parser (G9 / PH-158)
 *
 * Design decision: no external library (see technical_depth Karar 1).
 * Parses the output of `git diff -p --no-ext-diff` as produced by the backend.
 *
 * Public API:
 *   parseUnifiedDiff(patch: string): Hunk[]
 *
 * Edge cases handled:
 *   - hunk-without-comma  `@@ -1 +1 @@`  → oldLines/newLines default to 1
 *   - `\ No newline at end of file`       → type='meta', does not advance line counters
 *   - rename-only patch (no hunks)        → []
 *   - mode-change-only patch              → []
 *   - empty string                        → []
 *   - `--- a/...` / `+++ b/...` headers  → silently skipped
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DiffLineType = "add" | "del" | "ctx" | "hunk-header" | "meta";

export interface DiffLine {
  /** Line classification. */
  type: DiffLineType;
  /** 1-based line number in the old file; null for add lines and hunk-header. */
  oldNo: number | null;
  /** 1-based line number in the new file; null for del lines and hunk-header. */
  newNo: number | null;
  /** Raw line content (without the leading +/-/space glyph for add/del/ctx). */
  content: string;
}

export interface Hunk {
  /** Raw hunk header string, e.g. `@@ -1,3 +1,4 @@ function foo() {` */
  header: string;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLine[];
}

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

/** Regex for standard  `@@ -a,b +c,d @@` and comma-less `@@ -a +c @@` forms. */
const HUNK_HEADER_RE = /^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)/;

/**
 * Build a Hunk shell (header + counters, empty `lines`) from a matched `@@`
 * header. Pure: mirrors the comma-less default (oldLines/newLines → 1) and the
 * normalized header re-emission of the inline original verbatim.
 */
function hunkFromMatch(match: RegExpExecArray): Hunk {
  // match[1..4] are captured groups from the regex; they exist when match is non-null
  const oldStart = parseInt(match[1] as string, 10);
  // comma-less form `@@ -1 +1 @@` → oldLines/newLines default to 1
  const oldLines = match[2] !== undefined ? parseInt(match[2], 10) : 1;
  const newStart = parseInt(match[3] as string, 10);
  const newLines = match[4] !== undefined ? parseInt(match[4], 10) : 1;
  const headerSuffix = match[5] ?? "";

  return {
    header: `@@ -${oldStart},${oldLines} +${newStart},${newLines} @@${headerSuffix}`,
    oldStart,
    oldLines,
    newStart,
    newLines,
    lines: [],
  };
}

/** Running 1-based old/new line cursors while consuming a hunk body. */
interface LineCursor {
  oldNo: number;
  newNo: number;
}

/**
 * Classify ONE hunk-body line into a {@link DiffLine}, mutating the cursor
 * exactly as the inline glyph dispatch did (add → newNo++, del → oldNo++, ctx /
 * unknown → both++, meta → neither). Behavior identical to the original
 * `if/else if` chain; extracted only to lower cognitive complexity.
 */
function classifyDiffLine(dataLine: string, cur: LineCursor): DiffLine {
  const first = dataLine[0] as string | undefined;

  if (first === "+") {
    return { type: "add", oldNo: null, newNo: cur.newNo++, content: dataLine.slice(1) };
  }
  if (first === "-") {
    return { type: "del", oldNo: cur.oldNo++, newNo: null, content: dataLine.slice(1) };
  }
  if (first === " ") {
    return { type: "ctx", oldNo: cur.oldNo++, newNo: cur.newNo++, content: dataLine.slice(1) };
  }
  if (dataLine.startsWith("\\ ")) {
    // `\ No newline at end of file` — meta line, no line number advance
    return { type: "meta", oldNo: null, newNo: null, content: dataLine.slice(2) };
  }
  // Unknown / empty line inside hunk — treat as context
  return { type: "ctx", oldNo: cur.oldNo++, newNo: cur.newNo++, content: dataLine };
}

/**
 * Consume the body lines of one hunk starting at `start`, appending to
 * `hunk.lines` until the next `@@` header or end of input. Returns the index of
 * the first unconsumed line (next-hunk header or past-EOF) — the caller resumes
 * its outer loop there. Behavior identical to the original inner `while`.
 */
function parseHunkBody(lines: string[], start: number, hunk: Hunk): number {
  let i = start;
  const cur: LineCursor = { oldNo: hunk.oldStart, newNo: hunk.newStart };

  while (i < lines.length) {
    // bounds-checked: i < lines.length guarantees element is string
    const dataLine = lines[i] as string;

    // Next hunk header → stop consuming
    if (HUNK_HEADER_RE.test(dataLine)) break;

    // Empty trailing line at EOF (common in splits)
    if (dataLine === "" && i === lines.length - 1) {
      i++;
      break;
    }

    hunk.lines.push(classifyDiffLine(dataLine, cur));
    i++;
  }

  return i;
}

/**
 * Parse a unified diff patch string into an array of Hunk objects.
 *
 * @param patch - Raw patch text as returned by the backend `FileDiff.patch` field.
 * @returns Array of parsed hunks; empty array when patch is empty or has no hunks.
 */
export function parseUnifiedDiff(patch: string): Hunk[] {
  if (!patch || patch.trim() === "") return [];

  const lines = patch.split("\n");
  const hunks: Hunk[] = [];

  let i = 0;

  // Skip leading file-header lines (diff --git, index, old mode, new mode,
  // rename from/to, similarity, --- a/, +++ b/).
  // We simply skip lines until we hit the first @@ hunk header.
  while (i < lines.length && !HUNK_HEADER_RE.test(lines[i] as string)) i++;

  // Parse each hunk
  while (i < lines.length) {
    // bounds-checked: i < lines.length guarantees element is string
    const match = HUNK_HEADER_RE.exec(lines[i] as string);
    if (!match) { i++; continue; }

    const hunk = hunkFromMatch(match);
    i = parseHunkBody(lines, i + 1, hunk); // skip the @@ header, then consume body
    hunks.push(hunk);
  }

  return hunks;
}
