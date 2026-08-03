/**
 * milestoneStatus.ts — PH-339: a PURE map from the English-stored milestone
 * status (planned|active|done) to its Turkish label + ProjectHub design-token
 * classes. Kept side-effect-free (no React) so it is unit-testable and shared by
 * BOTH the read-only MilestoneTimeline and the edit-mode SummaryEditor <select>.
 *
 * a11y (AC5): colour is NEVER the only differentiator — the timeline renders the
 * `trLabel` TEXT badge alongside the (aria-hidden) colour dot, so a
 * colour-blind / screen-reader user still gets the status. Tokens only (no baked
 * hexes): planned = neutral/muted, active = warning (amber), done = success
 * (green) — theme-aware (dark default + html.light) for free.
 */
import type { MilestoneStatus } from "@/types/api";

export interface MilestoneStatusMeta {
  /** Turkish display label (AC1/AC2 — the FE translates the English status). */
  trLabel: string;
  /** Text colour token for the label. */
  textClass: string;
  /** Background colour token for the (aria-hidden) status dot. */
  dotClass: string;
  /** Soft-tinted pill classes for the status badge (bg + text). */
  badgeClass: string;
}

/** Render order for the timeline legend + the editor <select> options. */
export const MILESTONE_STATUSES: readonly MilestoneStatus[] = [
  "planned",
  "active",
  "done",
] as const;

const META: Record<MilestoneStatus, MilestoneStatusMeta> = {
  planned: {
    trLabel: "Planlı",
    textClass: "text-text-muted",
    dotClass: "bg-hairline-strong",
    badgeClass: "bg-inset text-text-muted",
  },
  active: {
    trLabel: "Aktif",
    textClass: "text-warning",
    dotClass: "bg-warning",
    badgeClass: "bg-warning-soft text-warning",
  },
  done: {
    trLabel: "Tamamlandı",
    textClass: "text-success",
    dotClass: "bg-success",
    badgeClass: "bg-success-soft text-success",
  },
};

/**
 * Metadata for a milestone status. Defensive: an unknown/garbage value (a
 * backend enum drift) falls back to the neutral `planned` styling rather than
 * throwing or rendering an unstyled badge — the timeline never crashes on a
 * value it doesn't recognise.
 */
export function milestoneStatusMeta(status: MilestoneStatus): MilestoneStatusMeta {
  return META[status] ?? META.planned;
}
