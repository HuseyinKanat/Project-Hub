import { Wifi } from "lucide-react";

import { PRIORITY_DOT, TYPE_BADGE, cn, phaseActorLabel } from "@/lib/utils";
import type { TicketResponse } from "@/types/api";
import { PrVerdictBadge } from "./PrVerdictBadge";
import { isPrVerdictLabel, resolvePrVerdict } from "./prVerdict";

interface TicketCardProps {
  ticket: TicketResponse;
  highlight?: boolean;
  showUpdatedAt?: boolean;
}

export function TicketCard({ ticket, highlight, showUpdatedAt }: Readonly<TicketCardProps>) {
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

  // Kit strips the "jarwis-" prefix from the assignee display name.
  const assigneeLabel = ticket.assignee?.display_name.replace(/^jarwis-/, "");

  // PRDEV-1: PR-review verdict is derived from labels only (zero-fetch — the card
  // gets no per-ticket comment/attachment request). Null in direct-mode / pre-review
  // → no icon. The resolved verdict token is filtered OUT of the plain chip row below
  // so it is shown exactly once (as the icon), never double-listed (AC4 + AC8).
  const verdictMeta = resolvePrVerdict(ticket.labels);
  const visibleLabels = ticket.labels.filter((l) => !isPrVerdictLabel(l));

  return (
    <article
      className={cn(
        // .ticket — bg-raised, 11px radius, 9px gap, kit hover = cyan hairline + 1px lift
        "flex flex-col gap-[9px] rounded-[11px] border border-hairline bg-raised p-3 transition-all duration-base hover:border-hairline-cyan motion-safe:hover:-translate-y-px",
        // WS live-update pulse — app feature, additive (out of kit scope, retained)
        highlight && "ring-2 ring-accent border-accent bg-accent-soft"
      )}
    >
      {/* .t-top — mono key (+ verdict icon) + TypeChip */}
      <div className="flex items-center justify-between gap-1.5">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="font-mono text-xs text-text-muted">{ticket.key}</span>
          {verdictMeta && <PrVerdictBadge meta={verdictMeta} size="sm" />}
        </span>
        <span
          className={cn(
            // .type-chip — 5px radius, 7px px, 10px (text-2xs), 600 uppercase tracking-wide
            "rounded-[5px] px-[7px] py-0.5 text-2xs font-semibold uppercase tracking-wide",
            TYPE_BADGE[ticket.type] ?? "bg-raised text-text-secondary",
          )}
        >
          {ticket.type}
        </span>
      </div>

      {/* .t-title — 13.5px, 500, line-height 1.4 */}
      <h3 className="text-[13.5px] font-medium leading-[1.4] text-text-primary">{ticket.title}</h3>

      {/* .t-phase — kit order: BETWEEN title and meta */}
      {ticket.agent_phase && (
        <span className="inline-flex items-center gap-[7px] self-start rounded-[7px] border border-hairline-cyan bg-accent-soft px-2 py-1 font-mono text-[11px] text-accent">
          <i
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent motion-safe:animate-pulse"
          />
          <span className="truncate">
            {phaseActorLabel(ticket)} · {ticket.agent_phase.phase}
          </span>
        </span>
      )}

      {/* .t-meta — left: priority dot+label; right: wifi + updated · assignee */}
      <div className="flex items-center justify-between text-xs text-text-muted">
        {/* .priority — 8px dot + level in text-secondary */}
        <span className="flex items-center gap-1.5 text-text-secondary">
          <span
            aria-hidden="true"
            className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[ticket.priority])}
          />
          {ticket.priority}
        </span>
        {(assigneeLabel || (showUpdatedAt && ticket.updated_at)) && (
          <span className="flex items-center gap-1.5">
            {showUpdatedAt && ticket.updated_at && (
              <Wifi aria-hidden="true" className="h-3 w-3 shrink-0" />
            )}
            <span className="truncate" title={ticket.assignee?.display_name}>
              {showUpdatedAt && ticket.updated_at && `${formatTime(ticket.updated_at)}`}
              {showUpdatedAt && ticket.updated_at && assigneeLabel ? " · " : ""}
              {assigneeLabel}
            </span>
          </span>
        )}
      </div>

      {/* .t-labels — up to 3 chips (verdict token filtered out; shown as icon above) */}
      {visibleLabels.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {visibleLabels.slice(0, 3).map((label) => (
            <span
              key={label}
              className="rounded-pill border border-hairline bg-raised px-[9px] py-0.5 text-[11px] text-text-secondary"
            >
              {label}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
