/**
 * MilestoneTimeline.tsx — PH-339: a PRESENTATIONAL, accessible visual timeline
 * of a board summary's milestones. Prop-driven (no data fetch — the owning
 * OverviewSummary holds the query and passes the array down), mirroring the
 * ProgressBar / SonarHealthPanel prop-driven pattern.
 *
 * Render (AC2): milestones are sorted by `order` ASCENDING and drawn as a
 * vertical timeline (a status-coloured rail dot per item). Each row shows the
 * title + (optional) target + (optional) due_date, and the status
 * planned/active/done via BOTH a colour (planned=gray, active=amber, done=green
 * — ProjectHub tokens, no baked hex) AND a text badge (`milestoneStatusMeta`).
 * An empty list renders a muted milestone empty-state.
 *
 * a11y (AC5): the timeline is a real `<ol>` (ordered list — the visual order is
 * semantic); each colour dot is `aria-hidden` and the human-readable status
 * TEXT badge carries the meaning, so colour is never the sole differentiator.
 */
import { milestoneStatusMeta } from "./milestoneStatus";
import { cn } from "@/lib/utils";
import type { Milestone } from "@/types/api";

/**
 * Format a date-only ISO "YYYY-MM-DD" as a Turkish long date. Parsed at UTC
 * midnight (date-only strings have no tz — a bare `new Date("2026-08-04")` is
 * UTC, but formatting must pin `timeZone:"UTC"` too or a negative-offset locale
 * shows the previous day). Falls back to the raw string for an unparseable value.
 */
function formatDueDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("tr-TR", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(d);
}

function MilestoneRow({
  milestone,
  isLast,
}: Readonly<{ milestone: Milestone; isLast: boolean }>) {
  const meta = milestoneStatusMeta(milestone.status);
  const due = formatDueDate(milestone.due_date);
  return (
    <li className="relative flex gap-3 pb-4 last:pb-0" data-testid="milestone-item">
      {/* Rail: a status-coloured dot + a connecting line to the next item
          (omitted on the last row so the rail doesn't dangle). */}
      <div className="relative flex flex-col items-center" aria-hidden="true">
        <span
          className={cn(
            "z-10 mt-1 h-3 w-3 shrink-0 rounded-full ring-2 ring-surface",
            meta.dotClass,
          )}
        />
        {!isLast && <span className="absolute top-2 h-full w-px bg-hairline" />}
      </div>

      <div className="min-w-0 flex-1 pb-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium text-text-primary">
            {milestone.title}
          </span>
          <span
            className={cn(
              "rounded-pill px-2 py-0.5 text-2xs font-medium",
              meta.badgeClass,
            )}
            data-testid="milestone-status"
          >
            {meta.trLabel}
          </span>
        </div>
        {(milestone.target || due) && (
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-text-muted">
            {milestone.target && (
              <span data-testid="milestone-target">{milestone.target}</span>
            )}
            {milestone.target && due && <span aria-hidden="true">·</span>}
            {due && (
              <time dateTime={milestone.due_date ?? undefined} className="mono">
                {due}
              </time>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export function MilestoneTimeline({
  milestones,
}: Readonly<{ milestones: Milestone[] }>) {
  const sorted = [...milestones].sort((a, b) => a.order - b.order);

  return (
    <section aria-labelledby="overview-milestones-heading" className="space-y-2">
      <h3
        id="overview-milestones-heading"
        className="eyebrow text-text-muted"
      >
        Kilometre Taşları
      </h3>
      {sorted.length === 0 ? (
        <p
          className="rounded-md border border-dashed border-hairline px-3 py-2.5 text-sm text-text-muted"
          data-testid="milestone-timeline-empty"
        >
          Henüz kilometre taşı eklenmemiş.
        </p>
      ) : (
        <ol className="mt-1" data-testid="milestone-timeline">
          {sorted.map((m, i) => (
            <MilestoneRow
              key={`${m.order}-${m.title}-${i}`}
              milestone={m}
              isLast={i === sorted.length - 1}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
