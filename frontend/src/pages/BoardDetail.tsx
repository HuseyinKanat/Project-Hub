import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Settings } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { BranchGraph } from "@/components/git";
import { LiveStatus, type LiveStatusValue } from "@/components/LiveStatus";
import { NewTicketDialog } from "@/components/NewTicketDialog";
import { TicketCard } from "@/components/TicketCard";
import { useWebSocket, isGitSyncedMessage } from "@/hooks/useWebSocket";
import { cn } from "@/lib/utils";
import { resolveStateColor } from "@/lib/stateColor";
import { useAuth } from "@/stores/auth";
import type { TicketResponse, WorkflowState } from "@/types/api";

export function BoardDetailPage() {
  const { boardKey = "" } = useParams<{ boardKey: string }>();
  const queryClient = useQueryClient();
  const location = useLocation();
  const [successToast, setSuccessToast] = useState<string | null>(
    (location.state as { toast?: string } | null)?.toast ?? null
  );

  // Tab strip: "kanban" | "graph" — persisted in location.hash
  const initialTab = (): "kanban" | "graph" => {
    if (typeof window !== "undefined" && window.location.hash === "#graph") return "graph";
    return "kanban";
  };
  const [activeTab, setActiveTab] = useState<"kanban" | "graph">(initialTab);

  // Highlighted shas from WS git_synced (3-s pulse in BranchGraph)
  const [highlightedShas, setHighlightedShas] = useState<Set<string>>(new Set());
  const highlightShasTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const switchTab = (tab: "kanban" | "graph") => {
    setActiveTab(tab);
    window.history.replaceState(null, "", tab === "graph" ? "#graph" : "#kanban");
  };

  useEffect(() => {
    if (successToast) {
      const t = setTimeout(() => setSuccessToast(null), 4000);
      // Clear router state so it doesn't re-fire on remount
      window.history.replaceState({}, "");
      return () => clearTimeout(t);
    }
  }, [successToast]);

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
      // PH-159 (G10): live graph sync — invalidate git graph on git_synced events
      if (isGitSyncedMessage(message)) {
        void queryClient.invalidateQueries({
          queryKey: ["git", boardKey, "graph"],
          refetchType: "active",
        });
        void queryClient.invalidateQueries({
          queryKey: ["git", boardKey, "status"],
          refetchType: "active",
        });
        const newShas = new Set(message.payload.new_commit_shas);
        if (newShas.size > 0) {
          setHighlightedShas(newShas);
          if (highlightShasTimerRef.current) clearTimeout(highlightShasTimerRef.current);
          highlightShasTimerRef.current = setTimeout(() => {
            setHighlightedShas(new Set());
          }, 3000);
        }
        return;
      }

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

  // PH-180: derive the board-header LiveStatus pill from the EXISTING board WS
  // instance (no second connection). The native `title` is preserved verbatim
  // so the connection-indicator e2e specs (ph-159 TC-7, ph-160 TC-9,
  // websocket-token-consistency) stay green.
  const liveStatus: LiveStatusValue = isConnected
    ? "live"
    : isConnecting
      ? "connecting"
      : "off";
  const liveStatusTitle = isConnected
    ? "Live updates active"
    : isConnecting
      ? "Connecting..."
      : "Disconnected";

  if (boardQuery.isLoading || ticketsQuery.isLoading) {
    return (
      <section className="flex items-center justify-center p-8">
        <div className="text-center">
          <div className="mb-4 h-8 w-8 animate-spin rounded-full border-4 border-hairline border-t-accent"></div>
          <p className="text-text-secondary">Board yükleniyor...</p>
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      {successToast && (
        <div
          role="status"
          className="fixed top-4 right-4 z-50 rounded border border-hairline bg-success-soft px-4 py-2 text-success shadow-md"
          data-testid="success-toast"
        >
          {successToast}
        </div>
      )}
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <Link to="/" className="text-xs text-text-muted hover:text-accent">
            ← Boards
          </Link>
          <h1 className="text-xl font-semibold tracking-tight text-text-primary sm:text-2xl">
            {boardQuery.data?.name ?? boardKey}
          </h1>
          {boardQuery.data?.description && (
            <p className="text-sm text-text-secondary">{boardQuery.data.description}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* PH-180: real-WS-driven connection pill, board header placement */}
          <LiveStatus status={liveStatus} title={liveStatusTitle} />

          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1 text-sm"
            onClick={() => setDialogOpen(true)}
          >
            <Plus className="h-4 w-4" /> Yeni ticket
          </button>
          <Link
            to={`/boards/${boardKey}/settings`}
            className="btn-secondary inline-flex items-center gap-1 text-sm"
            title="Board Settings"
          >
            <Settings className="h-4 w-4" />
            <span className="hidden sm:inline">Settings</span>
          </Link>
          <span className="rounded border border-hairline bg-raised px-2 py-1 font-mono text-xs text-text-secondary">
            {boardQuery.data?.key ?? boardKey}
          </span>
        </div>
      </header>

      <NewTicketDialog
        boardKey={boardKey}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />

      {(boardQuery.error || ticketsQuery.error) && (
        <div className="rounded-md border border-hairline bg-danger-soft px-3 py-2 text-sm text-danger">
          {((boardQuery.error ?? ticketsQuery.error) as Error).message}
        </div>
      )}

      {/* Tab strip — Kanban | Branch Graph (PH-159 G10) */}
      <div
        className="flex gap-1 border-b border-hairline"
        role="tablist"
        aria-label="Board views"
      >
        <button
          id="tab-kanban"
          type="button"
          role="tab"
          aria-selected={activeTab === "kanban"}
          aria-controls="panel-kanban"
          onClick={() => switchTab("kanban")}
          className={cn(
            "relative px-4 py-2 text-sm font-medium transition-colors focus:outline-none",
            activeTab === "kanban"
              ? "border-b-2 border-accent text-accent"
              : "text-text-secondary hover:text-text-primary",
          )}
        >
          Kanban
        </button>
        <button
          id="tab-graph"
          type="button"
          role="tab"
          aria-selected={activeTab === "graph"}
          aria-controls="panel-graph"
          onClick={() => switchTab("graph")}
          className={cn(
            "relative px-4 py-2 text-sm font-medium transition-colors focus:outline-none",
            activeTab === "graph"
              ? "border-b-2 border-accent text-accent"
              : "text-text-secondary hover:text-text-primary",
          )}
        >
          Branch Graph
        </button>
      </div>

      {/* Kanban panel — untouched */}
      {activeTab === "kanban" && (
        <div
          id="panel-kanban"
          role="tabpanel"
          aria-labelledby="tab-kanban"
          className="-mx-6 overflow-x-auto px-6 sm:mx-0 sm:px-0"
        >
          <div className="grid min-w-max grid-flow-col auto-cols-[14rem] gap-3 sm:auto-cols-[16rem]">
            {states.map((state) => {
              const list = ticketsByState[state.name] ?? [];
              const tone = resolveStateColor(state);
              return (
                <div
                  key={state.name}
                  data-testid={`kanban-column-${state.name}`}
                  className={cn(
                    "flex flex-col rounded-lg border border-hairline bg-surface p-2 ring-1 ring-hairline",
                    tone.className,
                  )}
                  style={tone.style}
                >
                  <div className="flex items-center justify-between px-1 pb-2">
                    <span className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide">
                      <span
                        aria-hidden="true"
                        className="h-2 w-2 rounded-full bg-current"
                      />
                      {state.name.replace(/_/g, " ")}
                    </span>
                    <span className="rounded-pill border border-hairline bg-raised px-1.5 font-mono text-2xs text-text-muted">
                      {list.length}
                    </span>
                  </div>
                  <div className="flex flex-1 flex-col gap-2">
                    {list.map((ticket) => (
                      <Link
                        key={ticket.id}
                        to={`/boards/${boardKey}/tickets/${ticket.key}`}
                        className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      >
                        <TicketCard
                          ticket={ticket}
                          highlight={highlightedTicketId === ticket.id}
                          showUpdatedAt={isConnected}
                        />
                      </Link>
                    ))}
                    {list.length === 0 && (
                      <div className="rounded-md border border-dashed border-hairline p-3 text-center text-2xs text-text-muted">
                        Boş
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Branch Graph panel — PH-167 SourceTree-style rework (self-contained 3-pane) */}
      {activeTab === "graph" && (
        <div
          id="panel-graph"
          role="tabpanel"
          aria-labelledby="tab-graph"
        >
          <BranchGraph
            boardKey={boardKey}
            highlightedShas={highlightedShas}
          />
        </div>
      )}
    </section>
  );
}
