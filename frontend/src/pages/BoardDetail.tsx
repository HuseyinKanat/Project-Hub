import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Settings, Wifi, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { BoardSettingsDialog } from "@/components/BoardSettingsDialog";
import { NewTicketDialog } from "@/components/NewTicketDialog";
import { TicketCard } from "@/components/TicketCard";
import { useWebSocket } from "@/hooks/useWebSocket";
import { STATE_CATEGORIES, cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth";
import type { TicketResponse, WorkflowState } from "@/types/api";

export function BoardDetailPage() {
  const { boardKey = "" } = useParams<{ boardKey: string }>();
  const queryClient = useQueryClient();

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

  const token = useAuth((s) => s.token) ?? "";

  const [liveTickets, setLiveTickets] = useState<TicketResponse[]>([]);
  const [highlightedTicketId, setHighlightedTicketId] = useState<string | null>(null);

  const REFETCH_EVENTS = new Set([
    "created", "deleted",
    "state_changed", "assigned", "unassigned",
    "claimed", "released",
    "field_changed", "phase_updated", "agent_phase_updated",
    "comment_added",
    "git_commit_linked", "git_pr_linked", "git_pr_merged",
  ]);

  const { isConnected, isConnecting } = useWebSocket({
    boardId: boardKey,
    token,
    onMessage: (message) => {
      const ticketKey = message.ticket_key;
      setHighlightedTicketId(message.ticket_id);

      if (message.type === "created" && message.ticket_key) {
        void api.getTicket(message.ticket_key).then((newTicket) => {
          setLiveTickets((prev) => {
            if (prev.some((t) => t.id === newTicket.id)) return prev;
            return [...prev, newTicket];
          });
          queryClient.setQueryData(
            ["tickets", boardKey],
            (old: { tickets: TicketResponse[] } | undefined) => {
              if (!old) return old;
              if (old.tickets.some((t) => t.id === newTicket.id)) return old;
              return { ...old, tickets: [...old.tickets, newTicket] };
            }
          );
        });
        return;
      }

      if (message.type === "deleted") {
        setLiveTickets((prev) => prev.filter((t) => t.id !== message.ticket_id));
        queryClient.setQueryData(
          ["tickets", boardKey],
          (old: { tickets: TicketResponse[] } | undefined) => {
            if (!old) return old;
            return { ...old, tickets: old.tickets.filter((t) => t.id !== message.ticket_id) };
          }
        );
        return;
      }

      if (REFETCH_EVENTS.has(message.type) && ticketKey) {
        void api.getTicket(ticketKey).then((updated) => {
          setLiveTickets((prev) =>
            prev.map((t) => (t.id === message.ticket_id ? updated : t))
          );
          queryClient.setQueryData(
            ["tickets", boardKey],
            (old: { tickets: TicketResponse[] } | undefined) => {
              if (!old) return old;
              return {
                ...old,
                tickets: old.tickets.map((t) =>
                  t.id === message.ticket_id ? updated : t
                ),
              };
            }
          );
          queryClient.setQueryData(["ticket", ticketKey], updated);
        });
      }
    },
  });

  useEffect(() => {
    if (ticketsQuery.data?.tickets) {
      setLiveTickets(ticketsQuery.data.tickets);
    }
  }, [ticketsQuery.data]);

  // Clear highlight after animation
  useEffect(() => {
    if (highlightedTicketId) {
      const timer = setTimeout(() => {
        setHighlightedTicketId(null);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [highlightedTicketId]);

  const ticketsByState = useMemo(() => {
    const groups: Record<string, TicketResponse[]> = {};
    for (const ticket of liveTickets) {
      (groups[ticket.state] ??= []).push(ticket);
    }
    return groups;
  }, [liveTickets]);

  const states: WorkflowState[] = boardQuery.data?.workflow.states ?? [];
  const [dialogOpen, setDialogOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  if (boardQuery.isLoading || ticketsQuery.isLoading) {
    return (
      <section className="flex items-center justify-center p-8">
        <div className="text-center">
          <div className="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-slate-300 border-t-slate-600"></div>
          <p className="text-slate-600">Board yükleniyor...</p>
        </div>
      </section>
    );
  }

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
        <div className="flex items-center gap-2">
          {/* WebSocket Connection Status */}
          <div
            className={cn(
              "flex items-center gap-1 rounded px-2 py-1 text-xs",
              isConnected
                ? "bg-green-100 text-green-700"
                : isConnecting
                  ? "bg-yellow-100 text-yellow-700"
                  : "bg-red-100 text-red-700"
            )}
            title={isConnected ? "Live updates active" : isConnecting ? "Connecting..." : "Disconnected"}
          >
            {isConnected ? (
              <Wifi className="h-3 w-3" />
            ) : (
              <WifiOff className="h-3 w-3" />
            )}
            <span>{isConnected ? "Live" : isConnecting ? "..." : "Off"}</span>
          </div>

          <Link to={`/boards/${boardKey}/settings`}>
            <button
              type="button"
              className="btn-ghost inline-flex items-center gap-1 text-sm"
            >
              <Settings className="h-4 w-4" /> Settings
            </button>
          </Link>
          <button
            type="button"
            className="btn-ghost inline-flex items-center gap-1 text-sm"
            onClick={() => setSettingsOpen(true)}
            title="Board ayarları"
          >
            <Settings className="h-4 w-4" />
          </button>

          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1 text-sm"
            onClick={() => setDialogOpen(true)}
          >
            <Plus className="h-4 w-4" /> Yeni ticket
          </button>
          <span className="rounded bg-slate-900 px-2 py-1 font-mono text-xs text-white">
            {boardQuery.data?.key ?? boardKey}
          </span>
        </div>
      </header>

      <NewTicketDialog
        boardKey={boardKey}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />

      {boardQuery.data && (
        <BoardSettingsDialog
          board={boardQuery.data}
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
        />
      )}

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
                    <Link
                      key={ticket.id}
                      to={`/boards/${boardKey}/tickets/${ticket.key}`}
                      className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                    >
                      <TicketCard
                        ticket={ticket}
                        highlight={highlightedTicketId === ticket.id}
                        showUpdatedAt={isConnected}
                      />
                    </Link>
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
