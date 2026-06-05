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
      return "bg-success-soft";
    case "del":
      return "bg-danger-soft";
    case "meta":
      return "bg-inset italic text-text-muted";
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
      return "text-success select-none";
    case "del":
      return "text-danger select-none";
    default:
      return "text-text-muted select-none";
  }
}

export function HunkView({ hunk, collapseThreshold = 50 }: HunkViewProps) {
  const shouldCollapse = hunk.lines.length > collapseThreshold;
  const [collapsed, setCollapsed] = useState(shouldCollapse);

  return (
    <div className="border-b border-hairline last:border-b-0">
      {/* Hunk header */}
      <div
        className="flex items-center gap-2 bg-inset px-3 py-1 font-mono text-xs text-text-muted"
        aria-label={`Hunk: ${hunk.header}`}
      >
        <span className="text-info font-semibold shrink-0">@@</span>
        <span className="truncate">{hunk.header.replace(/^@@/, "").trim()}</span>
        {shouldCollapse && (
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="ml-auto shrink-0 rounded px-2 py-0.5 text-xs font-medium text-accent hover:bg-accent-soft transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
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
                    className="w-10 min-w-[2.5rem] select-none text-right px-2 text-text-muted border-r border-hairline"
                    aria-label={line.oldNo !== null ? `Old line ${line.oldNo}` : "No old line number"}
                  >
                    {line.oldNo !== null ? line.oldNo : ""}
                  </td>
                  {/* New line number */}
                  <td
                    className="w-10 min-w-[2.5rem] select-none text-right px-2 text-text-muted border-r border-hairline"
                    aria-label={line.newNo !== null ? `New line ${line.newNo}` : "No new line number"}
                  >
                    {line.newNo !== null ? line.newNo : ""}
                  </td>
                  {/* Glyph */}
                  <td className={cn("w-5 min-w-[1.25rem] select-none px-1 text-center", glyphColor(line.type))}>
                    {glyph(line.type)}
                  </td>
                  {/* Content */}
                  <td className="px-2 py-0 whitespace-pre font-mono text-xs text-text-primary">
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
