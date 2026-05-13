import { Activity, Wifi } from "lucide-react";

import { PRIORITY_DOT, TYPE_BADGE, cn } from "@/lib/utils";
import type { TicketResponse } from "@/types/api";

interface TicketCardProps {
  ticket: TicketResponse;
  highlight?: boolean;
  showUpdatedAt?: boolean;
}

export function TicketCard({ ticket, highlight, showUpdatedAt }: TicketCardProps) {
  // Format relative time
  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "now";
    if (diffMins < 60) return `${diffMins}m`;
    if (diffHours < 24) return `${diffHours}h`;
    return `${diffDays}d`;
  };

  return (
    <article
      className={cn(
        "card space-y-2 p-3 hover:border-slate-300 transition-all duration-300",
        highlight && "ring-2 ring-blue-400 border-blue-400 bg-blue-50/50"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-slate-500">{ticket.key}</span>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
            TYPE_BADGE[ticket.type] ?? "bg-slate-100 text-slate-700",
          )}
        >
          {ticket.type}
        </span>
      </div>
      <h3 className="text-sm font-medium leading-snug">{ticket.title}</h3>
      <div className="flex items-center justify-between text-xs text-slate-500">
        <div className="flex items-center gap-1.5">
          <span className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[ticket.priority])} />
          <span>{ticket.priority}</span>
        </div>
        <div className="flex items-center gap-2">
          {showUpdatedAt && ticket.updated_at && (
            <span className="flex items-center gap-0.5 text-[10px]">
              <Wifi className="h-2.5 w-2.5" />
              {formatTime(ticket.updated_at)}
            </span>
          )}
          {ticket.assignee && (
            <span className="truncate" title={ticket.assignee.display_name}>
              {ticket.assignee.display_name}
            </span>
          )}
        </div>
      </div>
      {ticket.agent_phase && (
        <div className="flex items-center gap-1.5 rounded bg-yellow-50 px-2 py-1 text-[11px] text-yellow-800 ring-1 ring-yellow-200">
          <Activity className="h-3 w-3 animate-pulse" />
          <span className="truncate">
            {ticket.agent_phase.agent_id} · {ticket.agent_phase.phase}
          </span>
        </div>
      )}
      {ticket.labels.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {ticket.labels.slice(0, 3).map((label) => (
            <span
              key={label}
              className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600"
            >
              {label}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
