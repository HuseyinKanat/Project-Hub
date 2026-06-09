/**
 * metricMeta.ts — PH-241 (epic PH-238, child C): the static METRIC_META map that
 * drives the in-app SonarQube quality dashboard (SonarDashboard.tsx). Pure data,
 * no JSX — unit-testable and free of React.
 *
 * Each entry pairs a `BoardHealth` field with a human-readable label +
 * canonical SonarQube definition (verbatim from the SonarQube docs), the unit
 * (which drives value formatting: count → integer, percent → `NN.N%`, gate →
 * pill), the good direction (used for the directional hint), the issue type for
 * drill-down (BUG/VULNERABILITY/CODE_SMELL → clickable card opening the
 * SonarIssueDrawer; null → informational card), and a Lucide icon.
 *
 * Ordering: quality_gate first (rendered as the hero), then the 6 grid metrics.
 */
import {
  Bug,
  ShieldAlert,
  Sparkles,
  Percent,
  Copy,
  Code2,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import type { BoardHealth, SonarIssueType } from "@/types/api";

/** The numeric/gate metric keys carried by `BoardHealth` (excludes fetched_at). */
export type MetricKey = Exclude<keyof BoardHealth, "fetched_at">;

export interface MetricMeta {
  /** The `BoardHealth` field this card reads. */
  key: MetricKey;
  /** Display label (Title Case). */
  label: string;
  /** Canonical SonarQube definition — human-readable, shown under the value. */
  description: string;
  /** Drives value formatting: count → integer, percent → `NN.N%`, gate → pill. */
  unit: "count" | "percent" | "gate";
  /** Direction that is "good" — drives the directional hint copy. */
  goodDirection: "lower" | "higher" | "pass" | "none";
  /**
   * Issue type for code-linked drill-down. Non-null → the card is a focusable
   * button opening the SonarIssueDrawer; null → an informational card (no list
   * backs it — coverage / duplications / ncloc / quality gate).
   */
  issueType: SonarIssueType | null;
  /** Lucide icon for the card header. */
  icon: LucideIcon;
}

/** Short directional hint copy, keyed by `goodDirection`. */
export const DIRECTION_HINT: Record<MetricMeta["goodDirection"], string> = {
  lower: "Lower is better",
  higher: "Higher is better",
  pass: "Pass / fail",
  none: "Informational",
};

/**
 * The quality-gate metadata, rendered as the dashboard hero (not a grid card).
 * Exported separately so consumers get a non-`undefined` type without indexing
 * METRIC_META[0] (noUncheckedIndexedAccess).
 */
export const GATE_META: MetricMeta = {
  key: "quality_gate_status",
  label: "Quality Gate",
  description:
    "Pass/fail summary of the project against its quality conditions. Fails if any condition (e.g. new bugs, coverage threshold) is breached.",
  unit: "gate",
  goodDirection: "pass",
  issueType: null,
  icon: ShieldCheck,
};

/**
 * Ordered metric metadata. Index 0 (quality_gate_status) is rendered as the
 * hero; the remaining 6 fill the responsive card grid in this order.
 */
export const METRIC_META: readonly MetricMeta[] = [
  GATE_META,
  {
    key: "bugs",
    label: "Bugs",
    description:
      "Code that is demonstrably wrong and will produce incorrect behavior at runtime. Lower is better.",
    unit: "count",
    goodDirection: "lower",
    issueType: "BUG",
    icon: Bug,
  },
  {
    key: "vulnerabilities",
    label: "Vulnerabilities",
    description:
      "Security-sensitive code that could be exploited. Lower is better — review each one.",
    unit: "count",
    goodDirection: "lower",
    issueType: "VULNERABILITY",
    icon: ShieldAlert,
  },
  {
    key: "code_smells",
    label: "Code Smells",
    description:
      "Maintainability issues that make the code harder to change, though it still works. Lower is better.",
    unit: "count",
    goodDirection: "lower",
    issueType: "CODE_SMELL",
    icon: Sparkles,
  },
  {
    key: "coverage",
    label: "Coverage",
    description:
      "Percentage of lines exercised by automated tests. Higher is better.",
    unit: "percent",
    goodDirection: "higher",
    issueType: null,
    icon: Percent,
  },
  {
    key: "duplicated_lines_density",
    label: "Duplications",
    description:
      "Percentage of lines that are duplicated across the codebase. Lower is better.",
    unit: "percent",
    goodDirection: "lower",
    issueType: null,
    icon: Copy,
  },
  {
    key: "ncloc",
    label: "Lines of Code",
    description:
      "Non-comment lines of code analysed. Informational — indicates project size.",
    unit: "count",
    goodDirection: "none",
    issueType: null,
    icon: Code2,
  },
];

/**
 * Format a metric value for display.
 * - `null` → em-dash "—" (NO DATA — distinct from a real 0; Risk R1).
 * - percent → `NN.N%` (already a percent — do NOT ×100; mirrors SonarHealthPanel).
 * - count → integer string.
 * - gate values are not formatted here (the hero renders a pill).
 */
export function formatMetricValue(
  value: number | string | null,
  unit: MetricMeta["unit"],
): string {
  if (value == null) return "—";
  if (unit === "percent" && typeof value === "number") {
    return `${value.toFixed(1)}%`;
  }
  if (unit === "count" && typeof value === "number") {
    return String(value);
  }
  return String(value);
}
