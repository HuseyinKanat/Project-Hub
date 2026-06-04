/**
 * HunkView.tsx — renders a single unified diff hunk as a 3-column table.
 * G9 / PH-158
 *
 * Columns: old line no | new line no | content
 * Large hunks (> collapseThreshold lines) are collapsed by default.
 */
import { useState } from "react";

import { cn } from "@/lib/utils";
import type { DiffLine, Hunk } from "@/lib/diff/parseDiff";

interface HunkViewProps {
  hunk: Hunk;
  /** Lines above this count are collapsed by default. Default: 50. */
  collapseThreshold?: number;
}

/** Map DiffLine type → Tailwind row background classes (light + dark). */
function rowBg(type: DiffLine["type"]): string {
  switch (type) {
    case "add":
      return "bg-green-50 dark:bg-green-950/30";
    case "del":
      return "bg-red-50 dark:bg-red-950/30";
    case "meta":
      return "bg-slate-100 dark:bg-slate-800/60 italic text-slate-500 dark:text-slate-400";
    default:
      return ""; // ctx — transparent
  }
}

/** Leading glyph for each line type. */
function glyph(type: DiffLine["type"]): string {
  switch (type) {
    case "add":  return "+";
    case "del":  return "-";
    case "meta": return " ";
    default:     return " ";
  }
}

/** Text colour for the glyph. */
function glyphColor(type: DiffLine["type"]): string {
  switch (type) {
    case "add":
      return "text-green-700 dark:text-green-400 select-none";
    case "del":
      return "text-red-700 dark:text-red-400 select-none";
    default:
      return "text-slate-400 dark:text-slate-500 select-none";
  }
}

export function HunkView({ hunk, collapseThreshold = 50 }: HunkViewProps) {
  const shouldCollapse = hunk.lines.length > collapseThreshold;
  const [collapsed, setCollapsed] = useState(shouldCollapse);

  return (
    <div className="border-b border-slate-200 dark:border-slate-700 last:border-b-0">
      {/* Hunk header */}
      <div
        className="flex items-center gap-2 bg-slate-100 px-3 py-1 dark:bg-slate-800/80 font-mono text-xs text-slate-500 dark:text-slate-400"
        aria-label={`Hunk: ${hunk.header}`}
      >
        <span className="text-blue-600 dark:text-blue-400 font-semibold shrink-0">@@</span>
        <span className="truncate">{hunk.header.replace(/^@@/, "").trim()}</span>
        {shouldCollapse && (
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="ml-auto shrink-0 rounded px-2 py-0.5 text-xs font-medium text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-900/30 transition-colors"
            aria-expanded={!collapsed}
          >
            {collapsed
              ? `Expand hunk (${hunk.lines.length} lines)`
              : "Collapse hunk"}
          </button>
        )}
      </div>

      {/* Lines table */}
      {!collapsed && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-xs leading-5" role="table" aria-label="Diff lines">
            <tbody>
              {hunk.lines.map((line, idx) => (
                <tr
                  key={idx}
                  className={cn("group", rowBg(line.type))}
                  aria-label={`${line.type} line`}
                >
                  {/* Old line number */}
                  <td
                    className="w-10 min-w-[2.5rem] select-none text-right px-2 text-slate-400 dark:text-slate-500 border-r border-slate-200 dark:border-slate-700/50"
                    aria-label={line.oldNo !== null ? `Old line ${line.oldNo}` : "No old line number"}
                  >
                    {line.oldNo !== null ? line.oldNo : ""}
                  </td>
                  {/* New line number */}
                  <td
                    className="w-10 min-w-[2.5rem] select-none text-right px-2 text-slate-400 dark:text-slate-500 border-r border-slate-200 dark:border-slate-700/50"
                    aria-label={line.newNo !== null ? `New line ${line.newNo}` : "No new line number"}
                  >
                    {line.newNo !== null ? line.newNo : ""}
                  </td>
                  {/* Glyph */}
                  <td className={cn("w-5 min-w-[1.25rem] select-none px-1 text-center", glyphColor(line.type))}>
                    {glyph(line.type)}
                  </td>
                  {/* Content */}
                  <td className="px-2 py-0 whitespace-pre font-mono text-xs text-slate-800 dark:text-slate-200">
                    {line.content}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
