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
  while (i < lines.length) {
    // bounds-checked: i < lines.length guarantees element is string
    const line = lines[i] as string;
    if (HUNK_HEADER_RE.test(line)) break;
    i++;
  }

  // Parse each hunk
  while (i < lines.length) {
    // bounds-checked: i < lines.length guarantees element is string
    const line = lines[i] as string;
    if (!line) { i++; continue; }

    const match = HUNK_HEADER_RE.exec(line);
    if (!match) { i++; continue; }

    // match[1..4] are captured groups from the regex; they exist when match is non-null
    const oldStart = parseInt(match[1] as string, 10);
    // comma-less form `@@ -1 +1 @@` → oldLines/newLines default to 1
    const oldLines = match[2] !== undefined ? parseInt(match[2], 10) : 1;
    const newStart = parseInt(match[3] as string, 10);
    const newLines = match[4] !== undefined ? parseInt(match[4], 10) : 1;
    const headerSuffix = match[5] ?? "";

    const hunk: Hunk = {
      header: `@@ -${oldStart},${oldLines} +${newStart},${newLines} @@${headerSuffix}`,
      oldStart,
      oldLines,
      newStart,
      newLines,
      lines: [],
    };

    i++; // advance past the @@ header line

    let oldNo = oldStart;
    let newNo = newStart;

    // Consume lines until we hit the next @@ or end of input
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

      const first = dataLine[0] as string | undefined;

      if (first === "+") {
        hunk.lines.push({ type: "add", oldNo: null, newNo: newNo++, content: dataLine.slice(1) });
      } else if (first === "-") {
        hunk.lines.push({ type: "del", oldNo: oldNo++, newNo: null, content: dataLine.slice(1) });
      } else if (first === " ") {
        hunk.lines.push({ type: "ctx", oldNo: oldNo++, newNo: newNo++, content: dataLine.slice(1) });
      } else if (dataLine.startsWith("\\ ")) {
        // `\ No newline at end of file` — meta line, no line number advance
        hunk.lines.push({ type: "meta", oldNo: null, newNo: null, content: dataLine.slice(2) });
      } else {
        // Unknown / empty line inside hunk — treat as context
        hunk.lines.push({ type: "ctx", oldNo: oldNo++, newNo: newNo++, content: dataLine });
      }

      i++;
    }

    hunks.push(hunk);
  }

  return hunks;
}
