/**
 * EpicProgressPanel.tsx — PH-335 (Theme B, derived epic-progress rollup).
 *
 * A per-board progress strip mounted ABOVE the BoardDetail tab strip (kanban
 * untouched). Owns its OWN `["board", boardKey, "epic-progress"]` query
 * (api.getEpicProgress) — self-contained so a progress-endpoint failure degrades
 * INLINE and NON-BLOCKING (UC-01 E1) while the rest of BoardDetail stays fully
 * usable. It renders:
 *   - a board-level rollup line (AC5): "N/M done" + weighted % + a bar + the
 *     state distribution histogram (state dağılımı — council scope);
 *   - one compact row per epic (AC5): bar + "N/M done" + %;
 *   - an "Ungrouped" bucket row, shown ONLY when it holds tickets (AC4).
 *
 * Graceful degrade (AC4): a child-less epic reports 0/0 with an empty bar (the
 * backend div-by-zero guard means weighted_pct is 0, never NaN); a board with no
 * tickets shows a muted empty state; no white screen / no NaN anywhere.
 *
 * PER-BOARD ONLY — cross-board rollup is the explicitly deferred P4 (NOT here).
 * NO knobs (no per-assignee/label slicing, no configurable weighting/bucket) —
 * council-binding scope trim.
 *
 * Design system: ProjectHub tokens only (card, eyebrow, mono, bg-inset/raised,
 * border-hairline, text-text-*, text-accent/success/warning, rounded-pill) —
 * theme-aware for free.
 */
import type { ReactNode } from "react";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { stateTokenColor } from "@/lib/stateColor";
import type { EpicProgressBucket } from "@/types/api";

import { ProgressBar } from "./ProgressBar";

/** "45%" — weighted_pct rounded to a whole percent. */
function pctText(bucket: EpicProgressBucket): string {
  return `${Math.round(bucket.weighted_pct)}%`;
}

/**
 * State distribution (state dağılımı) — one chip per PRESENT state (the backend
 * histogram already omits zero-count states). A canonical state gets its
 * theme-aware `--state-<name>` dot; an unknown/custom state falls back to a
 * neutral hairline dot (never an empty CSS var). Rendered on the board rollup.
 */
function StateHistogram({
  histogram,
}: Readonly<{ histogram: Record<string, number> }>) {
  const entries = Object.entries(histogram).sort((a, b) =>
    a[0].localeCompare(b[0]),
  );
  if (entries.length === 0) return null;
  return (
    <ul
      className="flex flex-wrap items-center gap-x-3 gap-y-1"
      aria-label="State distribution"
    >
      {entries.map(([name, count]) => {
        const dot = stateTokenColor(name);
        return (
          <li
            key={name}
            className="flex items-center gap-1 text-2xs text-text-muted"
          >
            <span
              aria-hidden="true"
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                !dot && "bg-hairline-strong",
              )}
              style={dot ? { backgroundColor: dot } : undefined}
            />
            <span className="capitalize">{name.replace(/_/g, " ")}</span>
            <span className="mono text-text-secondary">{count}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** A bar + "N/M done" + % row, reused by each epic and the ungrouped bucket. */
function BucketRow({
  title,
  bucket,
  barLabel,
}: Readonly<{
  title: ReactNode;
  bucket: EpicProgressBucket;
  barLabel: string;
}>) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0 flex-1 text-sm text-text-primary">{title}</div>
        <div className="flex shrink-0 items-baseline gap-2">
          <span className="mono text-xs text-text-secondary">
            {bucket.done}/{bucket.total} done
          </span>
          <span className="text-2xs text-text-muted">{pctText(bucket)}</span>
        </div>
      </div>
      <ProgressBar pct={bucket.weighted_pct} label={barLabel} />
    </div>
  );
}

export function EpicProgressPanel({
  boardKey,
}: Readonly<{ boardKey: string }>) {
  const query = useQuery({
    queryKey: ["board", boardKey, "epic-progress"],
    queryFn: () => api.getEpicProgress(boardKey),
    enabled: Boolean(boardKey),
  });

  // Loading — a compact busy strip; never blocks the rest of BoardDetail.
  if (query.isLoading) {
    return (
      <section
        className="card flex flex-col gap-2 px-4 py-3"
        aria-busy="true"
        aria-label="Epic progress loading"
        data-testid="epic-progress-loading"
      >
        <span className="eyebrow text-text-muted">Epic Progress</span>
        <div className="h-2 w-full animate-pulse rounded-pill bg-inset" />
      </section>
    );
  }

  // Error — INLINE + NON-BLOCKING (UC-01 E1): a dashed card with a retry, while
  // the rest of BoardDetail stays fully usable. `<output>` → implicit role=status.
  if (query.isError) {
    return (
      <output
        className="card flex flex-wrap items-center gap-2 border-dashed border-hairline px-4 py-2.5 text-sm text-text-muted"
        aria-label="Epic progress unavailable"
        data-testid="epic-progress-error"
      >
        <span className="eyebrow text-text-muted">Epic Progress</span>
        <span className="text-warning">Could not load progress</span>
        <span className="text-text-muted/70">
          · {(query.error as Error).message}
        </span>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="ml-auto text-2xs font-medium text-accent hover:text-accent-hover"
        >
          Retry
        </button>
      </output>
    );
  }

  const data = query.data;
  if (!data) return null;

  const hasData =
    data.epics.length > 0 || data.ungrouped.total > 0 || data.board.total > 0;

  // Empty — a board with no non-deleted tickets at all (graceful, no bars/NaN).
  if (!hasData) {
    return (
      <output
        className="card flex items-center gap-2 border-dashed border-hairline px-4 py-2.5 text-sm text-text-muted"
        aria-label="No epic progress yet"
        data-testid="epic-progress-empty"
      >
        <span className="eyebrow text-text-muted">Epic Progress</span>
        <span>No tickets yet — progress appears once tickets are added.</span>
      </output>
    );
  }

  return (
    <section
      className="card flex flex-col gap-3 px-4 py-3"
      aria-label="Epic progress"
      data-testid="epic-progress-panel"
    >
      {/* Board-level rollup line (AC5) — the emphasized summary + distribution. */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="eyebrow text-text-muted">Epic Progress</span>
          <span className="mono text-xs font-semibold text-text-primary">
            {data.board.done}/{data.board.total} done
            <span className="ml-2 font-normal text-text-muted">
              {pctText(data.board)}
            </span>
          </span>
        </div>
        <ProgressBar pct={data.board.weighted_pct} label="Board progress" />
        <StateHistogram histogram={data.board.state_histogram} />
      </div>

      {/* Per-epic rows (AC5). Scrolls only when the list is long — no knob. */}
      {data.epics.length > 0 && (
        <ul className="flex max-h-72 flex-col gap-2.5 overflow-y-auto border-t border-hairline pt-3">
          {data.epics.map((epic) => (
            <li key={epic.epic_id}>
              <BucketRow
                bucket={epic}
                barLabel={`${epic.epic_key} progress`}
                title={
                  <span className="flex items-baseline gap-2">
                    <span className="mono shrink-0 text-xs text-accent">
                      {epic.epic_key}
                    </span>
                    <span className="truncate text-text-secondary">
                      {epic.epic_title}
                    </span>
                  </span>
                }
              />
            </li>
          ))}
        </ul>
      )}

      {/* Ungrouped bucket — only when it actually holds tickets (AC4). */}
      {data.ungrouped.total > 0 && (
        <div className="border-t border-hairline pt-3">
          <BucketRow
            bucket={data.ungrouped}
            barLabel="Ungrouped tickets progress"
            title={
              <span className="text-text-secondary">
                Ungrouped
                <span className="ml-1.5 text-2xs text-text-muted">(no epic)</span>
              </span>
            }
          />
        </div>
      )}
    </section>
  );
}
