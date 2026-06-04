/**
 * BranchLegend.tsx — PH-159 (G10)
 *
 * Left-rail branch list with selection state.
 * Default branch (main) is highlighted; clicking any entry sets selectedBranch state.
 * G11 will consume the selectedBranch callback for the branch detail panel.
 */

import { cn } from "@/lib/utils";
import type { GitBranchEntry } from "@/types/git";
import { laneColor } from "./branchGraphLayout";

interface BranchLegendProps {
  branches: GitBranchEntry[];
  selected: string | null;
  onSelect: (branch: string) => void;
}

export function BranchLegend({ branches, selected, onSelect }: BranchLegendProps) {
  // Sort: default first, then alphabetical
  const sorted = [...branches].sort((a, b) => {
    if (a.is_default && !b.is_default) return -1;
    if (!a.is_default && b.is_default) return 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <aside
      aria-label="Branch legend"
      className="flex w-48 flex-col gap-0.5 overflow-y-auto rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-800"
    >
      <h2 className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
        Branches
      </h2>
      {sorted.map((branch, idx) => {
        // Lane index: default → 0, others by sort order (1, 2, …)
        const laneIdx = branch.is_default
          ? 0
          : sorted.findIndex((b) => !b.is_default && b.name === branch.name) +
            (sorted.some((b) => b.is_default) ? 1 : 0);
        const color = laneColor(laneIdx);
        const isSelected = selected === branch.name;

        return (
          <button
            key={branch.name}
            type="button"
            aria-pressed={isSelected}
            onClick={() => onSelect(branch.name)}
            className={cn(
              "flex items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors",
              "hover:bg-slate-50 dark:hover:bg-slate-700/50",
              isSelected
                ? "bg-indigo-50 font-semibold dark:bg-indigo-900/30"
                : "text-slate-700 dark:text-slate-300",
            )}
          >
            {/* Branch color dot */}
            <span
              className="h-2 w-2 flex-shrink-0 rounded-full"
              style={{ backgroundColor: color }}
              aria-hidden="true"
            />

            {/* Branch name — truncate long names */}
            <span
              className="min-w-0 flex-1 truncate"
              title={branch.name}
              style={isSelected ? { color } : undefined}
            >
              {branch.name}
            </span>

            {/* HEAD indicator for default branch */}
            {branch.is_default && (
              <span className="flex-shrink-0 rounded bg-slate-900 px-1 py-0.5 font-mono text-[9px] text-white dark:bg-slate-600">
                HEAD
              </span>
            )}
          </button>
        );
      })}

      {branches.length === 0 && (
        <p className="px-1 text-[11px] text-slate-400 dark:text-slate-500">
          No branches
        </p>
      )}
    </aside>
  );
}
