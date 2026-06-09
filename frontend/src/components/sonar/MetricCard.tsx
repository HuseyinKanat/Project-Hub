/**
 * MetricCard.tsx — PH-241 (epic PH-238, child C): one metric in the SonarQube
 * quality dashboard grid (SonarDashboard.tsx).
 *
 * Renders a metric's label + icon + formatted value + short static description
 * + directional hint. Issue-backed cards (bugs / vulnerabilities / code smells,
 * `issueType != null`) are real focusable `<button>` elements that open the
 * SonarIssueDrawer for their type — DISABLED when the count is 0 or null so we
 * never open an empty drawer (mirrors SonarHealthPanel.ClickableMetricTile,
 * Risk R1). Informational cards (coverage / duplications / ncloc) are plain
 * non-interactive `<div>`s (no tabstop, no role — no issue list backs them).
 *
 * Design system: Cyan-on-Black ProjectHub tokens only (`.card`, `.eyebrow`,
 * `.mono`, semantic `text-success/warning/danger`, `bg-*-soft`, `text-text-*`,
 * `bg-raised`, `border-hairline`, `rounded-*`). No baked hexes, no slate/indigo —
 * theme-aware (dark default + `html.light`) for free.
 */
import { cn } from "@/lib/utils";
import type { SonarIssueType } from "@/types/api";
import { DIRECTION_HINT, formatMetricValue, type MetricMeta } from "./metricMeta";

interface MetricCardProps {
  meta: MetricMeta;
  /** Reconciled numeric value (`useSonarLiveCounts ?? health.*` for counts). */
  value: number | null;
  /** Issue-backed cards only: open the drawer for this type. */
  onOpen?: (type: SonarIssueType) => void;
  /** Issue-backed cards only: register the trigger so focus can be restored. */
  triggerRef?: (el: HTMLButtonElement | null) => void;
}

/** Value tint: bugs/vulns/smells > 0 read as danger; everything else neutral. */
function valueTone(meta: MetricMeta, value: number | null): string | undefined {
  if (meta.issueType != null && (value ?? 0) > 0) return "text-danger";
  return undefined;
}

export function MetricCard({
  meta,
  value,
  onOpen,
  triggerRef,
}: Readonly<MetricCardProps>) {
  const { Icon, label } = { Icon: meta.icon, label: meta.label };
  const formatted = formatMetricValue(value, meta.unit);
  const tone = valueTone(meta, value);

  const inner = (
    <>
      <div className="flex items-center gap-2">
        <Icon
          className="h-4 w-4 shrink-0 text-accent-strong"
          aria-hidden="true"
        />
        <span className="eyebrow text-text-muted">{label}</span>
      </div>
      <span
        className={cn(
          "mono text-3xl font-semibold leading-none text-text-primary",
          tone,
        )}
        data-testid={`sonar-metric-value-${meta.key}`}
      >
        {formatted}
      </span>
      <p className="text-xs leading-snug text-text-secondary">
        {meta.description}
      </p>
      <span className="eyebrow mt-auto text-text-muted/70">
        {DIRECTION_HINT[meta.goodDirection]}
      </span>
    </>
  );

  const baseClass =
    "card flex min-h-[148px] flex-col gap-2 p-4 text-left transition-colors";

  // Informational card (coverage / duplications / ncloc): non-interactive div.
  if (meta.issueType == null) {
    return (
      <div className={baseClass} data-testid={`sonar-metric-card-${meta.key}`}>
        {inner}
      </div>
    );
  }

  // Issue-backed card: a real focusable <button>. Disabled when count is 0/null
  // (never opens an empty drawer — Risk R1).
  const type = meta.issueType;
  const disabled = value == null || value === 0;
  const disabledValue = value == null ? "no data" : "0";
  const ariaLabel = disabled
    ? `${label}: ${disabledValue}`
    : `View ${value} ${label.toLowerCase()}`;

  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={() => onOpen?.(type)}
      disabled={disabled}
      aria-haspopup="dialog"
      aria-label={ariaLabel}
      data-testid={`sonar-metric-card-${meta.key}`}
      className={cn(
        baseClass,
        "enabled:cursor-pointer enabled:hover:bg-raised enabled:hover:border-accent/40",
        "disabled:cursor-default disabled:opacity-70",
      )}
    >
      {inner}
    </button>
  );
}
