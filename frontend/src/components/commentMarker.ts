/**
 * commentMarker.ts — PH-302
 *
 * Pure parser for the leading protocol marker Jarwis stamps on handoff / system
 * comments — e.g. "[HANDOFF pm→architect] ...", "[BLOCKED] ...",
 * "[DEPLOY-FAILED] reverted ...". Kept JSX-free and dependency-free so it can be
 * unit-tested with Node's built-in test runner (node:test + native TS strip, the
 * repo convention — see permissions.test.ts). CommentCard.tsx consumes it for
 * rendering. Marker vocabulary follows contracts/exit-protocol.md.
 */

export type CommentMarkerType =
  | "HANDOFF"
  | "BLOCKED"
  | "RECOVERY"
  | "DEPLOY"
  | "DEPLOY-FAILED"
  | "ESCALATION"
  | "EVIDENCE";

export interface CommentMarker {
  /** Canonical marker keyword. */
  type: CommentMarkerType;
  /** HANDOFF source endpoint (role / terminal token e.g. "done"), else null. */
  from: string | null;
  /** HANDOFF target endpoint, else null. */
  to: string | null;
}

export interface ParsedComment {
  /** Recognised leading marker, or null when the body carries none. */
  marker: CommentMarker | null;
  /**
   * Body with the recognised marker token stripped from the front and leading
   * whitespace trimmed. IDENTICAL to the input string when no marker matched —
   * so marker-less comments keep rendering byte-for-byte as before.
   */
  body: string;
}

// Payload-less markers. "DEPLOY-FAILED" must precede "DEPLOY" in the alternation
// so the longer keyword wins (else "[DEPLOY-FAILED]" would match "DEPLOY" and
// leave a dangling "-FAILED]").
const FIXED_MARKERS = [
  "DEPLOY-FAILED",
  "DEPLOY",
  "BLOCKED",
  "RECOVERY",
  "ESCALATION",
  "EVIDENCE",
] as const;

// "[HANDOFF <from> <arrow> <to>]" — arrow tolerates U+2192 (→) or ASCII "->".
// from/to are any run of non-"]" chars, surrounding whitespace absorbed.
const HANDOFF_RE = /^\s*\[HANDOFF\s+([^\]]+?)\s*(?:→|->)\s*([^\]]+?)\s*\]/;
const FIXED_RE = new RegExp(`^\\s*\\[(${FIXED_MARKERS.join("|")})\\]`);

/**
 * Parse the leading marker off a comment body. Never throws; returns
 * `{ marker: null, body }` (input untouched) when nothing matches.
 */
export function parseCommentMarker(body: string): ParsedComment {
  const handoff = HANDOFF_RE.exec(body);
  if (handoff) {
    const [full = "", from = "", to = ""] = handoff;
    return {
      marker: { type: "HANDOFF", from, to },
      body: body.slice(full.length).trimStart(),
    };
  }

  const fixed = FIXED_RE.exec(body);
  if (fixed) {
    const [full = "", keyword = ""] = fixed;
    return {
      marker: { type: keyword as CommentMarkerType, from: null, to: null },
      body: body.slice(full.length).trimStart(),
    };
  }

  return { marker: null, body };
}
