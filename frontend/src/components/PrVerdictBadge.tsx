import type { CSSProperties } from "react";

import type { PrVerdictMeta } from "./prVerdict";

/**
 * PrVerdictBadge.tsx — PRDEV-1
 *
 * A colour-coded PR-verdict pill (emoji + label) driven by `resolvePrVerdict`
 * (prVerdict.ts). Three sizes:
 *   • sm  → icon-only, for the kanban TicketCard. NON-interactive by design (the card
 *           is a <Link>, so a nested button/anchor is illegal HTML); rendered as a
 *           labelled `role="img"` <span>.
 *   • md  → emoji + label, for the ticket-detail header chip row.
 *   • lg  → emoji + label, larger, for the PrReviewPanel heading.
 * An optional `onClick` turns md/lg into a <button> (opens the full review report);
 * without it — or at size sm — the badge is a plain labelled <span>. Colours resolve at
 * paint time (var(--success|--warning|--danger|--lane-violet)) so they stay theme-aware.
 * Follows the CommentCard MARKER_META `color-mix` pill idiom and reuses the kit
 * `.status-pill` geometry.
 */

const SIZE_STYLE: Readonly<Record<"sm" | "md" | "lg", CSSProperties>> = {
  sm: { gap: 0, padding: "2px 6px", fontSize: 11 },
  md: { padding: "3px 9px", fontSize: 12 },
  lg: { padding: "5px 11px", fontSize: 13, fontWeight: 600 },
};

export function PrVerdictBadge({
  meta,
  size = "md",
  onClick,
  title,
}: Readonly<{
  meta: PrVerdictMeta;
  size?: "sm" | "md" | "lg";
  onClick?: () => void;
  title?: string;
}>) {
  const iconOnly = size === "sm";
  // Accessible text always carries the verdict word; the emoji is decorative.
  const a11yLabel = `PR incelemesi: ${meta.label}`;

  const style: CSSProperties = {
    color: meta.colorVar,
    background: `color-mix(in srgb, ${meta.colorVar} 14%, transparent)`,
    border: `1px solid color-mix(in srgb, ${meta.colorVar} 34%, transparent)`,
    ...SIZE_STYLE[size],
  };

  const inner = (
    <>
      <span aria-hidden="true">{meta.emoji}</span>
      {!iconOnly && <span>{meta.label}</span>}
    </>
  );

  // Interactive variant (md/lg with an onClick) — opens the full report. `sm` never
  // becomes a button (it lives inside a card <Link>).
  if (onClick && !iconOnly) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="status-pill mono transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        style={style}
        // visible text "meta.label" ⊂ aria-label → WCAG 2.5.3 label-in-name holds.
        aria-label={`${a11yLabel} — tam raporu aç`}
        title={title ?? a11yLabel}
      >
        {inner}
      </button>
    );
  }

  return (
    <span
      className="status-pill mono"
      style={style}
      role="img"
      aria-label={a11yLabel}
      title={title ?? a11yLabel}
    >
      {inner}
    </span>
  );
}
