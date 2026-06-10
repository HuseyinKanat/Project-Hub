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
 * Quality-gate pill descriptor keyed by the raw `quality_gate_status` value.
 * The SINGLE source of truth for gate label + cyan-on-black soft-tone — DRY'd
 * (PH-249, Risk R4) out of the three previously-duplicated copies in
 * SonarHealthPanel, SonarDashboard, and the new SonarRepoHealthCards. No baked
 * hexes — semantic `text-*`/`bg-*-soft` tokens only.
 */
export interface GateDescriptor {
  label: string;
  tone: string;
}
export const GATE_MAP: Record<string, GateDescriptor> = {
  OK: { label: "Passed", tone: "text-success bg-success-soft" },
  ERROR: { label: "Failed", tone: "text-danger bg-danger-soft" },
  WARN: { label: "Warning", tone: "text-warning bg-warning-soft" },
};

/** Fallback for a health row with an unknown / null gate status. */
export const GATE_UNKNOWN: GateDescriptor = {
  label: "Unknown",
  tone: "text-text-muted bg-raised",
};

/**
 * Resolve a raw `quality_gate_status` to its pill descriptor. `null`
 * (never-scanned / gate unknown) and any unmapped value fall back to
 * `GATE_UNKNOWN`. Keep all three sonar surfaces consistent by routing through
 * this helper rather than indexing GATE_MAP inline.
 */
export function resolveGate(status: string | null): GateDescriptor {
  if (status == null) return GATE_UNKNOWN;
  return GATE_MAP[status] ?? GATE_UNKNOWN;
}

/** Percent float 0..100 → `NN.N%`, null → em-dash. (Already a percent — do NOT ×100.) */
export function formatPercent(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(1)}%`;
}

/**
 * Relative "Synced 3m ago" timestamp from an ISO string. Tiny inline formatter
 * (no new dep) via Intl.RelativeTimeFormat; "" for unparseable input. Shared by
 * the sonar surfaces (PH-249) so the freshness copy stays uniform.
 */
export function relativeSynced(iso: string | null): string {
  if (iso == null) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.round((Date.now() - then) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let rel: string;
  if (sec < 60) rel = rtf.format(-sec, "second");
  else if (sec < 3600) rel = rtf.format(-Math.round(sec / 60), "minute");
  else if (sec < 86400) rel = rtf.format(-Math.round(sec / 3600), "hour");
  else rel = rtf.format(-Math.round(sec / 86400), "day");
  return `Synced ${rel}`;
}

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
