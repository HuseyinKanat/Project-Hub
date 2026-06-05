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
        "card space-y-2 p-3 transition-all duration-base hover:border-hairline-cyan motion-safe:hover:shadow-glow-cyan-sm",
        highlight && "ring-2 ring-accent border-accent bg-accent-soft"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-text-muted">{ticket.key}</span>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide",
            TYPE_BADGE[ticket.type] ?? "bg-raised text-text-secondary",
          )}
        >
          {ticket.type}
        </span>
      </div>
      <h3 className="text-sm font-medium leading-snug text-text-primary">{ticket.title}</h3>
      <div className="flex items-center justify-between text-xs text-text-muted">
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
        <div className="flex items-center gap-1.5 rounded-md bg-accent-soft px-2 py-1 font-mono text-2xs text-accent ring-1 ring-hairline-cyan">
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
              className="rounded-pill border border-hairline bg-raised px-2 py-0.5 text-2xs text-text-secondary"
            >
              {label}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
