/**
 * ProgressBar.tsx — PH-335 (epic-progress rollup): a presentational, accessible
 * progress bar. PURELY presentational (prop-driven, no data fetch) — the owning
 * EpicProgressPanel holds the query and passes `pct` down, mirroring the
 * SonarHealthPanel prop-driven pattern (components/SonarHealthPanel.tsx).
 *
 * a11y: a real `role="progressbar"` carrying aria-valuenow/valuemin/valuemax
 * (0..100) + an aria-label, so a screen reader announces "<label>: N". The value
 * is defensively clamped to 0..100 and coerced away from NaN/Infinity — the
 * backend already guards div-by-zero (child-less bucket → 0), but a CSS width and
 * an aria value must NEVER be a bad float.
 *
 * Design system: ProjectHub tokens only (bg-inset track, bg-accent / bg-success
 * fill, rounded-pill) — theme-aware (dark default + html.light) for free. No
 * baked hexes, no slate/indigo utilities.
 */
import { cn } from "@/lib/utils";

export function ProgressBar({
  pct,
  label,
  className,
}: Readonly<{
  /** Percent-complete 0..100 (clamped + NaN-guarded defensively). */
  pct: number;
  /** Accessible name announced with the value (e.g. "PH-271 progress"). */
  label?: string;
  /** Optional extra classes on the track (width/height overrides). */
  className?: string;
}>) {
  const safe = Number.isFinite(pct) ? pct : 0;
  const clamped = Math.max(0, Math.min(100, safe));
  const rounded = Math.round(clamped);
  const complete = clamped >= 100;
  return (
    <div
      role="progressbar"
      aria-valuenow={rounded}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cn(
        "h-2 w-full overflow-hidden rounded-pill bg-inset",
        className,
      )}
    >
      <div
        className={cn(
          "h-full rounded-pill transition-[width] duration-300",
          complete ? "bg-success" : "bg-accent",
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
