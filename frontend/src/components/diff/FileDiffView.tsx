/**
 * FileDiffView.tsx — presentational component for a single file diff.
 * G9 / PH-158
 *
 * Handles:
 *   - Binary files     → marker (no HunkView)
 *   - Truncated files  → marker (no HunkView)
 *   - Normal patch     → parseUnifiedDiff() → HunkView list
 *
 * Props: FileDiff from G8 types (read-only, backend contract)
 */
import { useState } from "react";

import { cn } from "@/lib/utils";
import type { FileDiff } from "@/types/git";
import { parseUnifiedDiff } from "@/lib/diff/parseDiff";
import { HunkView } from "./HunkView";

interface FileDiffViewProps {
  file: FileDiff;
  /** Lines-per-hunk collapse threshold passed to each HunkView. Default: 50. */
  collapseThreshold?: number;
  /** Whether the file card is initially expanded. Default: true. */
  defaultExpanded?: boolean;
}

/** Tailwind badge for change_type (A/M/D/R/C). */
function changeTypeBadge(type: string): string {
  switch (type.toUpperCase()) {
    case "A": return "bg-success-soft text-success";
    case "M": return "bg-info-soft text-info";
    case "D": return "bg-danger-soft text-danger";
    case "R": return "bg-accent-soft text-accent";
    case "C": return "bg-raised text-text-muted";
    default:  return "bg-raised text-text-muted";
  }
}

/** Human-readable label for change_type. */
function changeTypeLabel(type: string): string {
  switch (type.toUpperCase()) {
    case "A": return "Added";
    case "M": return "Modified";
    case "D": return "Deleted";
    case "R": return "Renamed";
    case "C": return "Copied";
    default:  return type;
  }
}

export function FileDiffView({
  file,
  collapseThreshold = 50,
  defaultExpanded = true,
}: Readonly<FileDiffViewProps>) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const hunks = file.patch ? parseUnifiedDiff(file.patch) : [];
  const displayPath = file.old_path && file.old_path !== file.path
    ? `${file.old_path} → ${file.path}`
    : file.path;

  return (
    <div className="overflow-hidden rounded-[10px] border border-hairline text-sm">
      {/* File header */}
      <div
        className="flex items-center gap-2 bg-raised px-3 py-2 cursor-pointer select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
        onClick={() => setExpanded((e) => !e)}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded((v) => !v); } }}
      >
        {/* Expand/collapse chevron */}
        <span
          className={cn(
            "text-text-muted transition-transform",
            expanded ? "rotate-90" : "rotate-0"
          )}
          aria-hidden="true"
        >
          ▶
        </span>

        {/* change_type badge */}
        <span
          className={cn("rounded px-1.5 py-0.5 text-xs font-semibold font-mono", changeTypeBadge(file.change_type))}
          title={changeTypeLabel(file.change_type)}
        >
          {file.change_type.toUpperCase()}
        </span>

        {/* File path */}
        <span className="font-mono text-xs font-medium text-text-secondary flex-1 truncate">
          {displayPath}
        </span>

        {/* Additions / deletions summary */}
        <span className="text-xs text-success font-mono">
          +{file.additions}
        </span>
        <span className="text-xs text-danger font-mono">
          -{file.deletions}
        </span>

        {/* Binary / truncated badges */}
        {file.is_binary && (
          <span className="rounded bg-raised px-1.5 py-0.5 text-xs text-text-muted">
            binary
          </span>
        )}
        {file.truncated && !file.is_binary && (
          <span className="rounded bg-warning-soft px-1.5 py-0.5 text-xs text-warning">
            truncated
          </span>
        )}
      </div>

      {/* Body — only when expanded */}
      {expanded && (
        <div>
          {file.is_binary ? (
            /* Binary marker */
            <div className="px-4 py-3 font-mono text-xs text-text-muted italic bg-surface">
              Binary file — preview unavailable
            </div>
          ) : file.truncated ? (
            /* Truncated marker — condition covers both patch=null and sliced non-null patch.
               Sliced data is intentionally NOT rendered (partial hunks are misleading). */
            <div className="px-4 py-3 font-mono text-xs text-warning bg-warning-soft">
              Diff truncated (file &gt;1 MiB)
            </div>
          ) : hunks.length === 0 ? (
            /* No hunks (rename-only, mode-change, or empty patch) */
            <div className="px-4 py-3 font-mono text-xs text-text-muted italic bg-surface">
              No content changes
            </div>
          ) : (
            /* Hunk list */
            <div className="divide-y divide-hairline bg-surface">
              {hunks.map((hunk, i) => (
                <HunkView
                  key={i}
                  hunk={hunk}
                  collapseThreshold={collapseThreshold}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
