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
    case "A": return "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300";
    case "M": return "bg-blue-100  text-blue-700  dark:bg-blue-900/40  dark:text-blue-300";
    case "D": return "bg-red-100   text-red-700   dark:bg-red-900/40   dark:text-red-300";
    case "R": return "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300";
    case "C": return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
    default:  return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400";
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
}: FileDiffViewProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const hunks = file.patch ? parseUnifiedDiff(file.patch) : [];
  const displayPath = file.old_path && file.old_path !== file.path
    ? `${file.old_path} → ${file.path}`
    : file.path;

  return (
    <div className="overflow-hidden rounded-md border border-slate-200 dark:border-slate-700 text-sm">
      {/* File header */}
      <div
        className="flex items-center gap-2 bg-slate-50 dark:bg-slate-800/60 px-3 py-2 cursor-pointer select-none"
        onClick={() => setExpanded((e) => !e)}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded((v) => !v); } }}
      >
        {/* Expand/collapse chevron */}
        <span
          className={cn(
            "text-slate-400 dark:text-slate-500 transition-transform",
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
        <span className="font-mono text-xs font-medium text-slate-700 dark:text-slate-200 flex-1 truncate">
          {displayPath}
        </span>

        {/* Additions / deletions summary */}
        <span className="text-xs text-green-600 dark:text-green-400 font-mono">
          +{file.additions}
        </span>
        <span className="text-xs text-red-600 dark:text-red-400 font-mono">
          -{file.deletions}
        </span>

        {/* Binary / truncated badges */}
        {file.is_binary && (
          <span className="rounded bg-slate-200 dark:bg-slate-700 px-1.5 py-0.5 text-xs text-slate-500 dark:text-slate-400">
            binary
          </span>
        )}
        {file.truncated && !file.is_binary && (
          <span className="rounded bg-yellow-100 dark:bg-yellow-900/40 px-1.5 py-0.5 text-xs text-yellow-700 dark:text-yellow-300">
            truncated
          </span>
        )}
      </div>

      {/* Body — only when expanded */}
      {expanded && (
        <div>
          {file.is_binary ? (
            /* Binary marker */
            <div className="px-4 py-3 font-mono text-xs text-slate-500 dark:text-slate-400 italic bg-white dark:bg-slate-900">
              Binary file — preview unavailable
            </div>
          ) : file.truncated ? (
            /* Truncated marker — condition covers both patch=null and sliced non-null patch.
               Sliced data is intentionally NOT rendered (partial hunks are misleading). */
            <div className="px-4 py-3 font-mono text-xs text-yellow-700 dark:text-yellow-300 bg-yellow-50 dark:bg-yellow-950/20">
              Diff truncated (file &gt;1 MiB)
            </div>
          ) : hunks.length === 0 ? (
            /* No hunks (rename-only, mode-change, or empty patch) */
            <div className="px-4 py-3 font-mono text-xs text-slate-400 dark:text-slate-500 italic bg-white dark:bg-slate-900">
              No content changes
            </div>
          ) : (
            /* Hunk list */
            <div className="divide-y divide-slate-200 dark:divide-slate-700 bg-white dark:bg-slate-900">
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
