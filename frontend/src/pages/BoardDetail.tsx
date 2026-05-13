import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { TicketCard } from "@/components/TicketCard";
import { STATE_CATEGORIES, cn } from "@/lib/utils";
import type { TicketResponse, WorkflowState } from "@/types/api";

export function BoardDetailPage() {
  const { boardKey = "" } = useParams<{ boardKey: string }>();

  const boardQuery = useQuery({
    queryKey: ["board", boardKey],
    queryFn: () => api.getBoard(boardKey),
    enabled: Boolean(boardKey),
  });

  const ticketsQuery = useQuery({
    queryKey: ["tickets", boardKey],
    queryFn: () => api.listTickets({ board_id: boardKey, limit: 100 }),
    enabled: Boolean(boardKey),
  });

  const ticketsByState = useMemo(() => {
    const groups: Record<string, TicketResponse[]> = {};
    for (const ticket of ticketsQuery.data?.tickets ?? []) {
      (groups[ticket.state] ??= []).push(ticket);
    }
    return groups;
  }, [ticketsQuery.data]);

  const states: WorkflowState[] = boardQuery.data?.workflow.states ?? [];

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <Link to="/" className="text-xs text-slate-500 hover:underline">
            ← Boards
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">
            {boardQuery.data?.name ?? boardKey}
          </h1>
          {boardQuery.data?.description && (
            <p className="text-sm text-slate-500">{boardQuery.data.description}</p>
          )}
        </div>
        <span className="rounded bg-slate-900 px-2 py-1 font-mono text-xs text-white">
          {boardQuery.data?.key ?? boardKey}
        </span>
      </header>

      {(boardQuery.error || ticketsQuery.error) && (
        <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {((boardQuery.error ?? ticketsQuery.error) as Error).message}
        </div>
      )}

      <div className="overflow-x-auto">
        <div className="grid min-w-max grid-flow-col auto-cols-[16rem] gap-3">
          {states.map((state) => {
            const list = ticketsByState[state.name] ?? [];
            return (
              <div
                key={state.name}
                className={cn(
                  "flex flex-col rounded-lg border p-2 ring-1",
                  STATE_CATEGORIES[state.name] ?? "bg-slate-50 ring-slate-200",
                )}
              >
                <div className="flex items-center justify-between px-1 pb-2 text-xs font-medium uppercase tracking-wide">
                  <span>{state.name.replace(/_/g, " ")}</span>
                  <span className="rounded-full bg-white/70 px-1.5 text-[10px] text-slate-700">
                    {list.length}
                  </span>
                </div>
                <div className="flex flex-1 flex-col gap-2">
                  {list.map((ticket) => (
                    <TicketCard key={ticket.id} ticket={ticket} />
                  ))}
                  {list.length === 0 && (
                    <div className="rounded-md border border-dashed border-slate-300 bg-white/40 p-3 text-center text-[11px] text-slate-500">
                      Boş
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
