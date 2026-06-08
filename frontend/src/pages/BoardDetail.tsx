import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Settings } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import { api } from "@/api/client";
import { BranchGraph, RepoSwitcher } from "@/components/git";
import { LiveStatus, type LiveStatusValue } from "@/components/LiveStatus";
import { NewTicketDialog } from "@/components/NewTicketDialog";
import { SonarHealthPanel } from "@/components/SonarHealthPanel";
import { TicketCard } from "@/components/TicketCard";
import { useWebSocket, isGitSyncedMessage } from "@/hooks/useWebSocket";
import { cn } from "@/lib/utils";
import { resolveStateColor, stateTokenColor } from "@/lib/stateColor";
import { useAuth } from "@/stores/auth";
import type { TicketResponse, WorkflowState } from "@/types/api";

type TicketsCache = { tickets: TicketResponse[] } | undefined;

// Cache updaters hoisted to module scope so the WS message handler stays under
// the nested-function-depth limit (typescript:S2004). Each is a pure transform
// of the existing tickets cache.
function appendTicketToCache(
  newTicket: TicketResponse,
): (old: TicketsCache) => TicketsCache {
  return (old) => {
    if (!old) return old;
    if (old.tickets.some((t) => t.id === newTicket.id)) return old;
    return { ...old, tickets: [...old.tickets, newTicket] };
  };
}

function removeTicketFromCache(
  ticketId: string,
): (old: TicketsCache) => TicketsCache {
  return (old) => {
    if (!old) return old;
    return { ...old, tickets: old.tickets.filter((t) => t.id !== ticketId) };
  };
}

function replaceTicketInCache(
  ticketId: string,
  updated: TicketResponse,
): (old: TicketsCache) => TicketsCache {
  return (old) => {
    if (!old) return old;
    return {
      ...old,
      tickets: old.tickets.map((t) => (t.id === ticketId ? updated : t)),
    };
  };
}

// Live-ticket state updaters hoisted to module scope so the WS message handler
// stays under the nested-function-depth limit (typescript:S2004). Each is a
// pure transform of the previous `liveTickets` array.
function upsertLiveTicket(
  newTicket: TicketResponse,
): (prev: TicketResponse[]) => TicketResponse[] {
  return (prev) =>
    prev.some((t) => t.id === newTicket.id) ? prev : [...prev, newTicket];
}

function removeLiveTicket(
  ticketId: string,
): (prev: TicketResponse[]) => TicketResponse[] {
  return (prev) => prev.filter((t) => t.id !== ticketId);
}

function replaceLiveTicket(
  ticketId: string,
  updated: TicketResponse,
): (prev: TicketResponse[]) => TicketResponse[] {
  return (prev) => prev.map((t) => (t.id === ticketId ? updated : t));
}

// react-query predicate matching a board's per-repo git query caches
// (['git', boardKey, repoKey, kind, ...] — PH-224). `kind` is one of 'graph' /
// 'status' / 'commits'. Used to invalidate ALL repos' graph/status on a
// board-wide git_synced WS envelope regardless of which repo segment they carry.
// Hoisted to module scope to keep the WS handler's nesting shallow (S2004).
function isBoardGitQuery(boardKey: string, kind: string) {
  return (query: { queryKey: unknown }): boolean => {
    const key = query.queryKey;
    return (
      Array.isArray(key) &&
      key[0] === "git" &&
      key[1] === boardKey &&
      key[3] === kind
    );
  };
}

// react-query predicate matching the per-board SonarQube issue caches
// (['board', boardKey, 'sonar-issues', ...]). Hoisted to module scope to keep
// the WS handler's nesting shallow (typescript:S2004).
function isSonarIssuesQuery(boardKey: string) {
  return (query: { queryKey: unknown }): boolean => {
    const key = query.queryKey;
    return (
      Array.isArray(key) &&
      key[0] === "board" &&
      key[1] === boardKey &&
      key[2] === "sonar-issues"
    );
  };
}

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

  // PH-224 (C4): multi-repo branch-view switcher. List the board's repos (the
  // settings page reuses this; cache it under a stable key). The list is cheap
  // and degrades gracefully — on error we fall back to single-repo (undefined
  // repo → backend primary), never blocking the Branch tab.
  const reposQuery = useQuery({
    queryKey: ["repositories", boardKey],
    queryFn: () => api.git.listRepositories(boardKey),
    enabled: Boolean(boardKey),
    staleTime: 60_000,
    retry: false,
  });
  const repositories = useMemo(
    () => reposQuery.data?.repositories ?? [],
    [reposQuery.data],
  );
  const primarySlug = useMemo(
    () => repositories.find((r) => r.is_primary)?.slug ?? repositories[0]?.slug,
    [repositories],
  );

  // `?repo=<slug>` is the source of truth for the selected repo (refresh-safe +
  // shareable, AC4). It coexists with the `#graph` hash tab. An unknown/absent
  // slug resolves to the primary; only valid (>1 repo) selections are honoured.
  const [searchParams, setSearchParams] = useSearchParams();
  const repoParam = searchParams.get("repo");
  const isMultiRepo = repositories.length > 1;
  const selectedRepo = useMemo(() => {
    if (!isMultiRepo) return undefined; // single-repo → primary (no param emitted)
    const known = repoParam && repositories.some((r) => r.slug === repoParam);
    return known ? repoParam : primarySlug;
  }, [isMultiRepo, repoParam, repositories, primarySlug]);

  // Clean up a stale/unknown `?repo=` (e.g. shared URL for a removed repo) so it
  // doesn't linger; replace-history to avoid a junk back-nav entry.
  useEffect(() => {
    if (!isMultiRepo) return;
    if (repoParam && !repositories.some((r) => r.slug === repoParam)) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete("repo");
          return next;
        },
        { replace: true },
      );
    }
  }, [isMultiRepo, repoParam, repositories, setSearchParams]);

  const handleRepoChange = (slug: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (slug === primarySlug) next.delete("repo");
        else next.set("repo", slug);
        return next;
      },
      { replace: false },
    );
  };

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
      // PH-159 (G10): live graph sync — invalidate git graph on git_synced events.
      // PH-224: keys gained a per-repo segment (['git', boardKey, repoKey, kind, …]),
      // so match by predicate on the kind slot (index 3) to invalidate every
      // repo's graph/status at once — a git_synced envelope is board-wide.
      if (isGitSyncedMessage(message)) {
        void queryClient.invalidateQueries({
          predicate: isBoardGitQuery(boardKey, "graph"),
          refetchType: "active",
        });
        void queryClient.invalidateQueries({
          predicate: isBoardGitQuery(boardKey, "status"),
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

      // PH-196: live SonarQube board-health sync. The board-level
      // `sonarqube_synced` envelope carries empty ticket_id/ticket_key
      // sentinels, so this branch MUST early-return — it must NOT fall through
      // to the ticket-refetch path below (which would `api.getTicket('')` on an
      // empty key). Invalidating the existing ["board", boardKey] query
      // refetches BoardResponse.health → SonarHealthPanel re-renders, no reload.
      if (message.type === "sonarqube_synced") {
        void queryClient.invalidateQueries({
          queryKey: ["board", boardKey],
          refetchType: "active",
        });
        // PH-218: the panel's live issue counts (useSonarLiveCounts) and the
        // drawer's list (useSonarIssues) live in the ['board', boardKey,
        // 'sonar-issues', ...] family. Refetch them on the same scan event so
        // the tile counts update live alongside board.health — predicate-scoped
        // so unrelated ['board', boardKey, ...] children stay untouched.
        void queryClient.invalidateQueries({
          predicate: isSonarIssuesQuery(boardKey),
          refetchType: "active",
        });
        return;
      }

      const ticketKey = message.ticket_key;
      setHighlightedTicketId(message.ticket_id);

      if (message.type === "created" && message.ticket_key) {
        const onCreated = (newTicket: TicketResponse) => {
          setLiveTickets(upsertLiveTicket(newTicket));
          queryClient.setQueryData(["tickets", boardKey], appendTicketToCache(newTicket));
        };
        void api.getTicket(message.ticket_key).then(onCreated);
        return;
      }

      if (message.type === "deleted") {
        const deletedId = message.ticket_id;
        setLiveTickets(removeLiveTicket(deletedId));
        queryClient.setQueryData(["tickets", boardKey], removeTicketFromCache(deletedId));
        return;
      }

      if (REFETCH_EVENTS.has(message.type) && ticketKey) {
        const updatedId = message.ticket_id;
        const onUpdated = (updated: TicketResponse) => {
          setLiveTickets(replaceLiveTicket(updatedId, updated));
          queryClient.setQueryData(["tickets", boardKey], replaceTicketInCache(updatedId, updated));
          queryClient.setQueryData(["ticket", ticketKey], updated);
        };
        void api.getTicket(ticketKey).then(onUpdated);
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
      const bucket = groups[ticket.state] ?? [];
      groups[ticket.state] = bucket;
      bucket.push(ticket);
    }
    return groups;
  }, [liveTickets]);

  const states: WorkflowState[] = boardQuery.data?.workflow.states ?? [];
  const [dialogOpen, setDialogOpen] = useState(false);

  // PH-180: derive the board-header LiveStatus pill from the EXISTING board WS
  // instance (no second connection). The native `title` is preserved verbatim
  // so the connection-indicator e2e specs (ph-159 TC-7, ph-160 TC-9,
  // websocket-token-consistency) stay green.
  let liveStatus: LiveStatusValue;
  let liveStatusTitle: string;
  if (isConnected) {
    liveStatus = "live";
    liveStatusTitle = "Live updates active";
  } else if (isConnecting) {
    liveStatus = "connecting";
    liveStatusTitle = "Connecting...";
  } else {
    liveStatus = "off";
    liveStatusTitle = "Disconnected";
  }

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
        <output
          className="fixed top-4 right-4 z-50 rounded border border-hairline bg-success-soft px-4 py-2 text-success shadow-md"
          data-testid="success-toast"
        >
          {successToast}
        </output>
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

      {/* PH-196: SonarQube board-health strip — board-wide, above the tab strip
          so it shows in BOTH Kanban and Branch Graph tabs. The empty state
          handles a null health (no scan / no project key). */}
      {boardQuery.data && (
        <SonarHealthPanel
          health={boardQuery.data.health ?? null}
          boardKey={boardKey}
          /* PH-235: feed the resolved key so the empty state distinguishes
             "no key" from "key set — no analysis yet". */
          projectKey={boardQuery.data.sonarqube_project_key ?? null}
        />
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
          {/* .kanban — horizontal flex row, 14px gap, top-aligned columns */}
          <div className="flex min-w-max items-start gap-[14px] pb-8 pt-2">
            {states.map((state) => {
              const list = ticketsByState[state.name] ?? [];
              const tone = resolveStateColor(state);
              // stateHex drives ONLY the column-div borderColor below (PH-98 e2e
              // contract). It is present only when the board stored the state
              // color as a 6-char hex (resolveStateColor style path).
              const stateHex = tone.style?.color as string | undefined;
              // gradColor drives the .kcol-head gradient + dot. Prefer the
              // theme-aware canonical `--state-<name>` token (consistent across
              // all 7 columns regardless of how the board stored state.color),
              // falling back to the board hex for non-canonical custom states.
              const gradColor = stateTokenColor(state.name) ?? stateHex;
              return (
                <div
                  key={state.name}
                  data-testid={`kanban-column-${state.name}`}
                  // .kcol — 300px flex column, single hairline, 14px radius, clipped.
                  // When the state has a user-set hex (resolveStateColor style path),
                  // tint the column border subtly with it (~30% alpha). This keeps the
                  // kit look (gradient header carries the main tint) AND restores the
                  // hex on the kanban-column testid element for the PH-98 e2e contract
                  // (workflow-state-color.spec asserts the column's inline style rgb).
                  className="flex w-[300px] shrink-0 flex-col overflow-hidden rounded-lg border border-hairline bg-surface max-h-[calc(100vh-230px)]"
                  style={stateHex ? { borderColor: stateHex + "4D" } : undefined}
                >
                  {/* .kcol-head — subtle state gradient + dot + name + count */}
                  <div
                    className="sticky top-0 flex items-center justify-between border-b border-hairline px-3.5 py-3"
                    style={
                      gradColor
                        ? {
                            background: `linear-gradient(180deg, color-mix(in srgb, ${gradColor} 7%, var(--bg-surface)), var(--bg-surface))`,
                          }
                        : undefined
                    }
                  >
                    <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                      <span
                        aria-hidden="true"
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: gradColor ?? "currentColor" }}
                      />
                      {state.name.replace(/_/g, " ")}
                    </span>
                    <span className="rounded-pill border border-hairline bg-raised px-2 py-px font-mono text-[11px] text-text-muted">
                      {list.length}
                    </span>
                  </div>
                  {/* .kcol-body — 10px padding + gap, own scroll */}
                  <div className="flex flex-1 flex-col gap-2.5 overflow-y-auto p-2.5">
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
                      <div className="rounded-md border border-dashed border-hairline p-[18px] text-center text-xs text-text-muted">
                        Empty
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Branch Graph panel — PH-167 SourceTree-style rework (self-contained 3-pane).
          PH-224: an optional RepoSwitcher sits ABOVE the graph for multi-repo
          boards; the selected repo slug is threaded into BranchGraph (queries +
          query keys) and used as its remount `key` so a switch resets selection.
          Single-repo boards render exactly as before (no switcher, repo=undefined,
          key stable) — zero layout shift / no extra markup. */}
      {activeTab === "graph" && (
        <div
          id="panel-graph"
          role="tabpanel"
          aria-labelledby="tab-graph"
        >
          {isMultiRepo && selectedRepo && (
            <RepoSwitcher
              repositories={repositories}
              value={selectedRepo}
              onChange={handleRepoChange}
            />
          )}
          <BranchGraph
            key={selectedRepo ?? "primary"}
            boardKey={boardKey}
            repo={selectedRepo}
            highlightedShas={highlightedShas}
          />
        </div>
      )}
    </section>
  );
}
