/**
 * CommitNode.tsx — PH-159 (G10)
 *
 * Custom xyflow node for a single git commit.
 * NOTE: xyflow-based BranchGraph was replaced by SourceTree-style list in PH-167.
 * This component is kept for reference / potential future use.
 * Renders: colored dot + short_sha + 1-line summary + ref badges + ticket_key links.
 */

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

/** Commit node data shape (previously from branchGraphLayout.ts; now local). */
export interface CommitNodeData extends Record<string, unknown> {
  sha: string;
  shortSha: string;
  summary: string;
  authorName: string;
  committedAt: string;
  refs: string[];
  ticketKeys: string[];
  lane: number;
  color: string;
  isNew: boolean;
  boardKey: string;
}

function CommitNodeInner({ data, selected }: NodeProps & { data: CommitNodeData }) {
  const {
    shortSha,
    summary,
    authorName,
    committedAt,
    refs,
    ticketKeys,
    color,
    isNew,
    boardKey,
  } = data as CommitNodeData;

  // Format relative time
  const relativeTime = formatRelativeTime(committedAt);

  // Refs: show first 2 inline, rest as +N chip
  const visibleRefs = refs.slice(0, 2);
  const extraRefs = refs.length - visibleRefs.length;

  return (
    <div
      className={cn(
        "relative flex items-start gap-2 rounded-lg border bg-white px-3 py-2 shadow-sm",
        "dark:bg-slate-800 dark:border-slate-700",
        "hover:shadow-md transition-shadow min-w-[180px] max-w-[200px]",
        selected && "ring-2 ring-indigo-500 ring-offset-1",
        isNew && "ring-2 ring-amber-400 ring-offset-1 animate-pulse",
      )}
    >
      {/* xyflow handles — invisible, needed for edge anchors */}
      <Handle
        type="target"
        position={Position.Top}
        style={{ opacity: 0, pointerEvents: "none", top: 0 }}
        isConnectable={false}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ opacity: 0, pointerEvents: "none", bottom: 0 }}
        isConnectable={false}
      />

      {/* Colored lane dot */}
      <span
        className="mt-1 h-3 w-3 flex-shrink-0 rounded-full border-2 border-white dark:border-slate-700 shadow-sm"
        style={{ backgroundColor: color }}
        aria-hidden="true"
      />

      <div className="min-w-0 flex-1">
        {/* sha + refs on one line */}
        <div className="flex flex-wrap items-center gap-1">
          <span className="font-mono text-[11px] text-slate-500 dark:text-slate-400">
            {shortSha}
          </span>
          {visibleRefs.map((ref) => (
            <span
              key={ref}
              className="rounded px-1 py-0.5 text-[10px] font-medium leading-none"
              style={{ backgroundColor: color + "33", color }}
              title={ref}
            >
              {ref.length > 14 ? `${ref.slice(0, 12)}…` : ref}
            </span>
          ))}
          {extraRefs > 0 && (
            <span className="rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-500 dark:bg-slate-700 dark:text-slate-400">
              +{extraRefs}
            </span>
          )}
        </div>

        {/* Commit summary */}
        <p className="mt-0.5 line-clamp-1 text-xs text-slate-800 dark:text-slate-200" title={summary}>
          {summary}
        </p>

        {/* Author + time */}
        <p className="mt-0.5 text-[10px] text-slate-400 dark:text-slate-500">
          {authorName} · {relativeTime}
        </p>

        {/* Ticket keys */}
        {ticketKeys.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {ticketKeys.map((key) => (
              <Link
                key={key}
                to={`/boards/${boardKey}/tickets/${key}`}
                onClick={(e) => e.stopPropagation()}
                className="rounded bg-indigo-50 px-1 py-0.5 text-[10px] font-medium text-indigo-700 hover:bg-indigo-100 dark:bg-indigo-900/30 dark:text-indigo-300 dark:hover:bg-indigo-900/50"
              >
                {key}
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export const CommitNode = memo(CommitNodeInner);

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------
function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = Date.now();
  const diff = now - date.getTime();
  const mins = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days = Math.floor(diff / 86_400_000);

  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}
