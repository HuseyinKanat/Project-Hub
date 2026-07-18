/**
 * prVerdict.ts — PRDEV-1
 *
 * Pure, JSX-free resolvers for the PR-Review UI surfaces (verdict badge, review
 * panel, kanban indicator). Dependency-free so it unit-tests with Node's built-in
 * runner (node:test + native TS strip) — the repo convention (see commentMarker.ts,
 * commentRoleChip.ts, grouping.ts). Consumed by PrVerdictBadge / PrReviewPanel /
 * TicketCard / TicketDetail for rendering.
 *
 * VERDICT SOURCE = ticket.labels (NOT the markdown report, NOT a comment scan). The
 * Coordinator's PR-review gate (Jarwis contracts/exit-protocol.md §8.PR) stamps ONE
 * verdict token onto the ticket labels; the UI reads it back with `resolvePrVerdict`.
 * A label-driven source is:
 *   • zero-fetch on the kanban — `TicketResponse` already carries `labels`, so a card
 *     needs no per-ticket comment/attachment request;
 *   • drift-proof — a closed token set is immune to emoji/markdown wording changes;
 *   • regression-safe FOR FREE — a direct-mode ticket never receives a verdict label,
 *     so `resolvePrVerdict` returns null → no badge, and the frontend never has to know
 *     the board's merge_strategy (absence-of-label gating).
 *
 * The severity counts (panel-only enrichment) and the SonarQube one-liner come from
 * the last pr-reviewer HANDOFF comment; the full findings come from the kind="review"
 * attachment. Both are strictly secondary — the verdict stands on the label alone.
 */

import type { CommentMarker } from "./commentMarker";

/** The four PR-review verdict outcomes (Jarwis §8.PR). */
export type PrVerdict = "safe" | "conditions" | "not_safe" | "needs_discussion";

export interface PrVerdictMeta {
  /** Canonical verdict enum. */
  verdict: PrVerdict;
  /** Status emoji (decorative — always paired with an accessible text label). */
  emoji: string;
  /** Short UPPERCASE badge label, e.g. "SAFE", "NOT SAFE". */
  label: string;
  /**
   * A CSS custom-property reference resolved at paint time so it stays theme-aware
   * (light/dark redefine the vars): success / warning / danger / lane-violet.
   */
  colorVar: string;
}

/**
 * Verdict LABEL vocabulary → verdict enum. MIRRORS the tokens the Coordinator writes
 * in Jarwis contracts/exit-protocol.md §8.PR (kept a single documented set on both
 * ends). `pr_changes_requested` is an ALIAS onto `not_safe` for backward-compat with
 * the pre-existing reviewer-reject label. Any other `pr_*` (or non-pr) token is
 * unknown → resolves to null → no badge.
 */
const LABEL_TO_VERDICT: Readonly<Record<string, PrVerdict>> = {
  pr_safe: "safe",
  pr_conditions: "conditions",
  pr_not_safe: "not_safe",
  pr_changes_requested: "not_safe", // alias — backward-compat with the reject label
  pr_needs_discussion: "needs_discussion",
};

/** Per-verdict presentation. Exhaustive over `PrVerdict` (a missing key is a compile error). */
const VERDICT_META: Readonly<Record<PrVerdict, PrVerdictMeta>> = {
  safe: { verdict: "safe", emoji: "✅", label: "SAFE", colorVar: "var(--success)" },
  conditions: { verdict: "conditions", emoji: "⚠️", label: "CONDITIONS", colorVar: "var(--warning)" },
  not_safe: { verdict: "not_safe", emoji: "❌", label: "NOT SAFE", colorVar: "var(--danger)" },
  needs_discussion: { verdict: "needs_discussion", emoji: "🛑", label: "DISCUSSION", colorVar: "var(--lane-violet)" },
};

/**
 * Tie-break priority when a ticket carries MORE THAN ONE verdict label (a stale token
 * that a re-review forgot to strip). The most cautious verdict wins so the UI never
 * downplays risk: not_safe > needs_discussion > conditions > safe.
 */
const VERDICT_PRIORITY: Readonly<Record<PrVerdict, number>> = {
  not_safe: 3,
  needs_discussion: 2,
  conditions: 1,
  safe: 0,
};

/**
 * Resolve a ticket's labels to its PR verdict presentation, or `null` when NO verdict
 * label is present (direct-mode / pre-review → no badge). Case- and whitespace-tolerant.
 * With multiple verdict labels the highest-`VERDICT_PRIORITY` one wins.
 */
export function resolvePrVerdict(
  labels: readonly string[] | null | undefined,
): PrVerdictMeta | null {
  if (!labels) return null;
  let best: PrVerdict | null = null;
  for (const raw of labels) {
    if (typeof raw !== "string") continue;
    const verdict = LABEL_TO_VERDICT[raw.trim().toLowerCase()];
    if (!verdict) continue;
    if (best === null || VERDICT_PRIORITY[verdict] > VERDICT_PRIORITY[best]) {
      best = verdict;
    }
  }
  return best === null ? null : VERDICT_META[best];
}

/**
 * True when a label is a PR-verdict token (any key of {@link LABEL_TO_VERDICT}). Lets a
 * plain label-chip list (ticket sidebar, kanban card) FILTER the verdict out so it is
 * shown exactly once — as the badge — never double-listed as a chip too (AC8).
 */
export function isPrVerdictLabel(label: string): boolean {
  return typeof label === "string" && Boolean(LABEL_TO_VERDICT[label.trim().toLowerCase()]);
}

export interface VerdictCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

/** Severity → its finding-count emoji (the documented `N🔴 M🟠 K🟡 J🔵` shape). */
const SEVERITY_EMOJI: Readonly<Record<keyof VerdictCounts, string>> = {
  critical: "🔴",
  high: "🟠",
  medium: "🟡",
  low: "🔵",
};

/**
 * Parse the severity finding counts out of a pr-reviewer HANDOFF comment body. Accepts
 * the documented number→emoji shape (`3🔴`, `3 🔴`) AND the reversed emoji→number order
 * (`🔴 3`) for robustness; the FIRST occurrence of each severity wins. A severity absent
 * from the body stays 0. Returns `null` when NO severity token is found at all — the
 * panel then HIDES the severity row (the counts are pure enrichment; the verdict badge +
 * report link stand on their own). Never throws.
 */
export function parseVerdictCounts(
  commentBody: string | null | undefined,
): VerdictCounts | null {
  if (!commentBody) return null;
  const counts: VerdictCounts = { critical: 0, high: 0, medium: 0, low: 0 };
  let found = false;
  for (const key of Object.keys(SEVERITY_EMOJI) as (keyof VerdictCounts)[]) {
    const emoji = SEVERITY_EMOJI[key];
    // `12🔴` (number-before) OR `🔴 12` (emoji-before); optional whitespace between.
    const re = new RegExp(`(?:(\\d+)\\s*${emoji})|(?:${emoji}\\s*(\\d+))`);
    const m = re.exec(commentBody);
    if (!m) continue;
    const n = Number.parseInt(m[1] ?? m[2] ?? "", 10);
    if (Number.isFinite(n)) {
      counts[key] = n;
      found = true;
    }
  }
  return found ? counts : null;
}

/**
 * Extract a one-line SonarQube summary from a pr-reviewer comment. Matches a
 * `sonar=<…>` / `sonar: <…>` / `SonarQube: <…>` token (case-insensitive) and returns
 * the remainder of that line, trimmed. Returns `null` when no sonar signal is present
 * (the panel hides the sonar row). Never throws.
 */
export function parseSonarSummary(
  commentBody: string | null | undefined,
): string | null {
  if (!commentBody) return null;
  const m = /sonar(?:qube)?\s*[=:]\s*([^\n\r]+)/i.exec(commentBody);
  if (!m) return null;
  const summary = (m[1] ?? "").trim();
  return summary.length > 0 ? summary : null;
}

/**
 * True when a parsed comment marker is the pr-reviewer's HANDOFF — a `HANDOFF` whose
 * `from` endpoint reads as the pr-reviewer role (tolerating `pr-reviewer`, `pr_reviewer`,
 * `pr reviewer` casings). The PrReviewPanel scans page comments for the LAST such marker
 * to source its severity + sonar enrichment.
 */
export function isPrReviewerHandoff(
  marker: CommentMarker | null | undefined,
): boolean {
  if (!marker || marker.type !== "HANDOFF" || !marker.from) return false;
  return /pr[-_ ]?reviewer/i.test(marker.from);
}
