import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, GitBranch, GitCommit, GitMerge, GitPullRequest, MessageSquarePlus, Wifi, WifiOff } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useWebSocket } from "@/hooks/useWebSocket";

import { ApiRequestError, api } from "@/api/client";
import { FieldEditor } from "@/components/FieldEditor";
import { MarkdownFieldEditor } from "@/components/MarkdownFieldEditor";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { PRIORITY_DOT, STATE_CATEGORIES, TYPE_BADGE, cn } from "@/lib/utils";
import type {
  ApiError,
  HistoryEntry,
  TicketResponse,
  TicketUpdatePayload,
  WorkflowState,
} from "@/types/api";

const TYPE_FIELDS: Record<
  string,
  Array<{ key: keyof TicketUpdatePayload; label: string; required?: boolean; description?: string }>
> = {
  feature: [
    { key: "acceptance_criteria", label: "Acceptance Criteria" },
    {
      key: "technical_depth",
      label: "Technical Depth",
      required: true,
      description:
        "İmplementasyon sırasında keşfedilen teknik borçlar, FIXME'ler. in_progress → in_review için zorunlu.",
    },
    { key: "impact_analysis", label: "Impact Analysis", description: "QA dolduracak." },
    { key: "test_plan", label: "Test Plan", description: "QA dolduracak." },
  ],
  task: [
    { key: "acceptance_criteria", label: "Acceptance Criteria" },
    {
      key: "technical_depth",
      label: "Technical Depth",
      required: true,
      description:
        "İmplementasyon sırasında keşfedilen teknik borçlar, FIXME'ler. in_progress → in_review için zorunlu.",
    },
    { key: "impact_analysis", label: "Impact Analysis", description: "QA dolduracak." },
    { key: "test_plan", label: "Test Plan", description: "QA dolduracak." },
  ],
  bug: [
    { key: "steps_to_reproduce", label: "Steps to Reproduce" },
    { key: "expected_behavior", label: "Expected Behavior" },
    { key: "actual_behavior", label: "Actual Behavior" },
    {
      key: "technical_depth",
      label: "Technical Depth",
      required: true,
      description: "Root cause analizi + fix yaklaşımı, kalan borçlar. in_progress → in_review için zorunlu.",
    },
    { key: "impact_analysis", label: "Impact Analysis", description: "QA dolduracak." },
    { key: "test_plan", label: "Test Plan", description: "QA dolduracak." },
  ],
  epic: [],
};

export function TicketDetailPage() {
  const { boardKey = "", ticketKey = "" } = useParams<{
    boardKey: string;
    ticketKey: string;
  }>();
  const qc = useQueryClient();

  const ticketQuery = useQuery({
    queryKey: ["ticket", ticketKey],
    queryFn: () => api.getTicket(ticketKey),
    enabled: Boolean(ticketKey),
  });
  const boardQuery = useQuery({
    queryKey: ["board", boardKey],
    queryFn: () => api.getBoard(boardKey),
    enabled: Boolean(boardKey),
  });
  const historyQuery = useQuery({
    queryKey: ["ticket-history", ticketKey],
    queryFn: () => api.listHistory(ticketKey),
    enabled: Boolean(ticketKey),
  });

  const token = localStorage.getItem("token") ?? "dev-token";
  const boardId = boardQuery.data?.id ?? "";

  const LIVE_EVENTS = new Set([
    "state_changed", "assigned", "unassigned", "claimed", "released",
    "field_changed", "phase_updated", "agent_phase_updated",
    "comment_added", "git_commit_linked", "git_pr_linked",
    "git_pr_merged", "git_branch_deleted",
  ]);

  const { isConnected, isConnecting } = useWebSocket({
    boardId,
    token,
    onMessage: (message) => {
      if (message.ticket_key !== ticketKey) return;
      if (LIVE_EVENTS.has(message.type)) {
        void api.getTicket(ticketKey).then((updated) => {
          qc.setQueryData(["ticket", ticketKey], updated);
        });
        qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
        if (message.type === "comment_added") {
          qc.invalidateQueries({ queryKey: ["ticket-comments", ticketKey] });
        }
      }
    },
  });

  const ticket = ticketQuery.data;
  const board = boardQuery.data;
  const states: WorkflowState[] = board?.workflow.states ?? [];
  const allowedTransitions = useMemo(() => {
    if (!ticket || !board) return [];
    return board.workflow.transitions
      .filter((t) => t.from === ticket.state || t.from === "*")
      .map((t) => t.to);
  }, [ticket, board]);

  const updateMutation = useMutation({
    mutationFn: (payload: TicketUpdatePayload) => api.updateTicket(ticketKey, payload),
    onSuccess: (updated) => {
      qc.setQueryData(["ticket", ticketKey], updated);
      qc.invalidateQueries({ queryKey: ["tickets", boardKey] });
      qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
    },
  });

  const [transitionError, setTransitionError] = useState<ApiError | null>(null);
  const transitionMutation = useMutation({
    mutationFn: (toState: string) => api.transitionTicket(ticketKey, toState),
    onSuccess: (updated) => {
      setTransitionError(null);
      qc.setQueryData(["ticket", ticketKey], updated);
      qc.invalidateQueries({ queryKey: ["tickets", boardKey] });
      qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.body) {
        setTransitionError(err.body);
      } else {
        setTransitionError({ error: "unknown", message: (err as Error).message });
      }
    },
  });

  if (ticketQuery.isLoading || boardQuery.isLoading) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">Yükleniyor…</p>;
  }
  if (ticketQuery.error) {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
        {(ticketQuery.error as Error).message}
      </div>
    );
  }
  if (!ticket || !board) return null;

  const typeFields = TYPE_FIELDS[ticket.type] ?? [];

  return (
    <section className="space-y-4">
      <header className="space-y-2">
        <Link
          to={`/boards/${boardKey}`}
          className="inline-flex items-center gap-1 text-xs text-slate-500 hover:underline dark:text-slate-400"
        >
          <ArrowLeft className="h-3 w-3" />
          {board.name}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-sm text-slate-500 dark:text-slate-400">{ticket.key}</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
              TYPE_BADGE[ticket.type] ?? "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300",
            )}
          >
            {ticket.type}
          </span>
          <h1 className="flex-1 text-2xl font-semibold tracking-tight dark:text-slate-100">{ticket.title}</h1>
          <div
            className={cn(
              "flex items-center gap-1 rounded px-2 py-0.5 text-[10px]",
              isConnected
                ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                : isConnecting
                  ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
                  : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
            )}
            title={isConnected ? "Live updates active" : isConnecting ? "Connecting..." : "Disconnected"}
          >
            {isConnected ? <Wifi className="h-2.5 w-2.5" /> : <WifiOff className="h-2.5 w-2.5" />}
            <span>{isConnected ? "Live" : isConnecting ? "…" : "Off"}</span>
          </div>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <div className="space-y-3">
          <FieldEditor
            label="Description"
            value={ticket.description || null}
            rows={4}
            onSave={async (v) => {
              await updateMutation.mutateAsync({ description: v ?? "" });
            }}
          />

          {typeFields.map((f) => (
            <div key={f.key as string} id={`field-${f.key as string}`} tabIndex={-1}>
              <MarkdownFieldEditor
                label={f.label}
                required={f.required}
                description={f.description}
                value={(ticket[f.key as keyof TicketResponse] as string | null) ?? null}
                onSave={async (v) => {
                  await updateMutation.mutateAsync({ [f.key]: v } as TicketUpdatePayload);
                }}
              />
            </div>
          ))}

          <CommentsBlock ticketKey={ticketKey} />
        </div>

        <aside className="space-y-3">
          {/* Quick Edit Fields Summary */}
          <div className="card p-3 space-y-2">
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Alanlar
            </h3>
            <div className="space-y-1.5">
              {typeFields.map((f) => {
                const value = ticket[f.key as keyof TicketResponse] as string | null;
                const isFilled = Boolean(value && value.trim().length > 0);
                return (
                  <button
                    key={f.key as string}
                    type="button"
                    onClick={() => {
                      const el = document.getElementById(`field-${f.key as string}`);
                      el?.scrollIntoView({ behavior: "smooth", block: "center" });
                      el?.focus();
                    }}
                    className={cn(
                      "w-full flex items-center justify-between rounded px-2 py-1.5 text-xs transition-colors",
                      "hover:bg-slate-100 dark:hover:bg-slate-700",
                      isFilled ? "text-slate-700 dark:text-slate-300" : "text-slate-400 dark:text-slate-500"
                    )}
                  >
                    <span className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          isFilled ? "bg-green-500" : "bg-slate-300 dark:bg-slate-600"
                        )}
                      />
                      {f.label}
                      {f.required && <span className="text-red-400">*</span>}
                    </span>
                    <span className="text-[10px]">
                      {isFilled ? "✓" : "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="card p-3 space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                State
              </label>
              <span
                className={cn(
                  "inline-block rounded-md px-2 py-1 text-xs font-medium ring-1",
                  STATE_CATEGORIES[ticket.state] ?? "bg-slate-50 text-slate-700 ring-slate-200",
                )}
              >
                {ticket.state.replace(/_/g, " ")}
              </span>
            </div>

            {allowedTransitions.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs text-slate-500 dark:text-slate-400">Geçiş</p>
                <div className="flex flex-wrap gap-1">
                  {allowedTransitions.map((to) => (
                    <button
                      key={to}
                      type="button"
                      className="btn-ghost text-xs ring-1 ring-slate-200 dark:ring-slate-600"
                      onClick={() => transitionMutation.mutate(to)}
                      disabled={transitionMutation.isPending}
                    >
                      → {to.replace(/_/g, " ")}
                    </button>
                  ))}
                </div>
                {transitionError && <TransitionErrorBanner error={transitionError} />}
              </div>
            )}
          </div>

          <div className="card p-3 text-xs space-y-2 dark:text-slate-300">
            <Row label="Priority">
              <span className="inline-flex items-center gap-1.5">
                <span className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[ticket.priority])} />
                {ticket.priority}
              </span>
            </Row>
            <Row label="Reporter">{ticket.reporter.display_name}</Row>
            <Row label="Assignee">{ticket.assignee?.display_name ?? "—"}</Row>
            <Row label="Labels">
              {ticket.labels.length === 0 ? (
                "—"
              ) : (
                <span className="flex flex-wrap gap-1">
                  {ticket.labels.map((l) => (
                    <span
                      key={l}
                      className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                    >
                      {l}
                    </span>
                  ))}
                </span>
              )}
            </Row>
            <Row label="Created">{new Date(ticket.created_at).toLocaleString()}</Row>
            {ticket.branch_name && (
              <Row label="Branch">
                <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700 dark:bg-slate-700 dark:text-slate-300">
                  <GitBranch className="h-3 w-3 shrink-0" />
                  {ticket.branch_name}
                </span>
              </Row>
            )}
          </div>

          {ticket.agent_phase && (
            <div className="card p-3 space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Agent Phase
              </p>
              <div className="flex items-center gap-1.5 rounded bg-yellow-50 px-2 py-1 text-xs text-yellow-800 ring-1 ring-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-300 dark:ring-yellow-800">
                <Activity className="h-3 w-3 animate-pulse" />
                <span>
                  {ticket.agent_phase.agent_id} · {ticket.agent_phase.phase}
                </span>
              </div>
              {ticket.agent_phase.message && (
                <p className="text-[11px] text-slate-600 dark:text-slate-400">{ticket.agent_phase.message}</p>
              )}
            </div>
          )}

          <HistoryBlock entries={historyQuery.data ?? []} />
        </aside>
      </div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="text-right text-slate-800 dark:text-slate-200">{children}</span>
    </div>
  );
}

function TransitionErrorBanner({ error }: { error: ApiError }) {
  const cls = "rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-400";
  if (error.error === "field_gate_not_met") {
    return (
      <div className={cls} role="alert">
        <p className="font-medium">Geçiş için eksik alan(lar):</p>
        <ul className="list-disc pl-4">
          {error.missing_fields?.map((f) => <li key={f}>{f}</li>)}
        </ul>
        <p className="mt-1 text-[11px] opacity-80">
          ({error.transition}) — alanı doldurup tekrar dene.
        </p>
      </div>
    );
  }
  if (error.error === "invalid_transition") {
    return (
      <div className={cls} role="alert">
        Geçersiz geçiş: {error.from_state} → {error.to_state}. İzin verilen:{" "}
        {error.allowed?.join(", ") ?? "—"}
      </div>
    );
  }
  return (
    <div className={cls} role="alert">
      {error.message ?? error.error}
    </div>
  );
}

function CommentsBlock({ ticketKey }: { ticketKey: string }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");

  const commentsQuery = useQuery({
    queryKey: ["ticket-comments", ticketKey],
    queryFn: () => api.listComments(ticketKey),
    enabled: Boolean(ticketKey),
  });

  const addMut = useMutation({
    mutationFn: () => api.addComment(ticketKey, body),
    onSuccess: () => {
      setBody("");
      qc.invalidateQueries({ queryKey: ["ticket-comments", ticketKey] });
      qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
    },
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    await addMut.mutateAsync();
  }

  const comments = commentsQuery.data ?? [];

  return (
    <section className="card p-3 space-y-3">
      <h3 className="flex items-center gap-1 text-sm font-semibold text-slate-800 dark:text-slate-200">
        <MessageSquarePlus className="h-4 w-4" /> Yorumlar ({comments.length})
      </h3>
      {commentsQuery.isLoading ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">Yükleniyor…</p>
      ) : comments.length === 0 ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">Henüz yorum yok.</p>
      ) : (
        <ol className="space-y-2">
          {comments.map((c) => (
            <li key={c.id} className="border-l-2 border-slate-200 pl-3 py-1 dark:border-slate-600">
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                <span className="font-medium text-slate-700 dark:text-slate-300">{c.author.display_name}</span>
                <span className="ml-2">{new Date(c.created_at).toLocaleString()}</span>
                {c.edited_at && <span className="ml-2 italic">(düzenlendi)</span>}
              </div>
              <div className="mt-0.5 whitespace-pre-wrap font-mono text-xs text-slate-800 dark:text-slate-300">
                {c.body}
              </div>
            </li>
          ))}
        </ol>
      )}
      <form onSubmit={submit} className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-600">
        <textarea
          className="input font-mono text-xs"
          rows={3}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Markdown destekli yorum…"
        />
        {addMut.error && (
          <p className="text-xs text-red-600 dark:text-red-400">{(addMut.error as Error).message}</p>
        )}
        <div className="flex justify-end">
          <button
            type="submit"
            className="btn-primary text-xs"
            disabled={addMut.isPending || body.trim().length === 0}
          >
            {addMut.isPending ? "Gönderiliyor…" : "Gönder"}
          </button>
        </div>
      </form>
    </section>
  );
}

function HistoryBlock({ entries }: { entries: HistoryEntry[] }) {
  return (
    <div className="card p-3 space-y-2">
      <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        Activity ({entries.length})
      </h3>
      {entries.length === 0 ? (
        <p className="text-xs text-slate-500 dark:text-slate-500">Henüz aktivite yok.</p>
      ) : (
        <ol className="space-y-1.5 text-xs">
          {entries.map((e) => {
            const isGit = e.event_type.startsWith("git_");
            const borderColor = isGit
              ? e.event_type.includes("invalid") || e.event_type.includes("non_conventional")
                ? "border-yellow-400 dark:border-yellow-600"
                : "border-blue-400 dark:border-blue-600"
              : "border-slate-200 dark:border-slate-600";
            return (
              <li key={e.id} className={`border-l-2 ${borderColor} pl-2`}>
                <div className="text-slate-500 dark:text-slate-400">
                  {new Date(e.created_at).toLocaleString()}
                  {e.actor && <span className="ml-1">· {e.actor.display_name}</span>}
                </div>
                {!isGit && (
                  <div className="text-slate-800 dark:text-slate-300">
                    <code className="text-[10px]">{e.event_type}</code>
                    {e.field && <span className="ml-1 text-slate-600 dark:text-slate-400">({e.field})</span>}
                    {renderChange(e)}
                  </div>
                )}
                <GitEventBadge entry={e} />
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function renderChange(e: HistoryEntry): React.ReactNode {
  if (e.event_type === "field_changed") {
    return (
      <span className="ml-1 text-slate-600 dark:text-slate-400">
        : <code className="text-[10px] rounded bg-slate-100 px-0.5 dark:bg-slate-700">{JSON.stringify(e.old_value)}</code> →{" "}
        <code className="text-[10px] rounded bg-slate-100 px-0.5 dark:bg-slate-700">{JSON.stringify(e.new_value)}</code>
      </span>
    );
  }
  return null;
}

function GitEventBadge({ entry }: { entry: HistoryEntry }) {
  const meta = entry.metadata as Record<string, string | number | boolean> | null;
  if (!meta) return null;

  if (entry.event_type === "git_commit_linked") {
    const sha = String(meta.sha_short ?? "");
    const msg = String(meta.message ?? "");
    const url = String(meta.url ?? "");
    const author = String(meta.author ?? "");
    const branch = String(meta.branch ?? "");
    const isConventional = Boolean(meta.is_conventional);
    return (
      <div className="mt-1 rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] space-y-0.5 dark:border-slate-600 dark:bg-slate-900">
        <div className="flex items-center gap-1.5 font-medium text-slate-700 dark:text-slate-300">
          <GitCommit className="h-3 w-3 shrink-0" />
          {url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" className="font-mono text-blue-600 hover:underline dark:text-blue-400">{sha}</a>
          ) : (
            <span className="font-mono">{sha}</span>
          )}
          {!isConventional && (
            <span className="rounded bg-yellow-100 px-1 text-[9px] text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">non-conventional</span>
          )}
        </div>
        <p className="text-slate-600 line-clamp-2 dark:text-slate-400">{msg}</p>
        <p className="text-slate-400 dark:text-slate-500">{author}{branch ? ` · ${branch}` : ""}</p>
      </div>
    );
  }

  if (entry.event_type === "git_commit_invalid_format") {
    return (
      <div className="mt-1 rounded border border-yellow-200 bg-yellow-50 px-2 py-1 text-[11px] text-yellow-700 dark:border-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400">
        ⚠ Non-conventional commit {meta.sha_short} — beklenen: <code className="dark:text-yellow-300">feat(PH-XX): ...</code>
      </div>
    );
  }

  if (entry.event_type === "git_pr_linked" || entry.event_type === "git_pr_merged" || entry.event_type === "git_pr_closed") {
    const isMerged = entry.event_type === "git_pr_merged";
    const icon = isMerged
      ? <GitMerge className="h-3 w-3 shrink-0 text-purple-500" />
      : <GitPullRequest className="h-3 w-3 shrink-0 text-blue-500" />;
    const warning = meta.warning ? String(meta.warning) : null;
    return (
      <div className="mt-1 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] space-y-0.5 dark:border-slate-600 dark:bg-slate-900">
        <div className="flex items-center gap-1.5 text-slate-700 dark:text-slate-300">
          {icon}
          {meta.pr_url ? (
            <a href={String(meta.pr_url)} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline dark:text-blue-400">
              #{meta.pr_number} {meta.pr_title}
            </a>
          ) : (
            <span>#{meta.pr_number} {meta.pr_title}</span>
          )}
          {isMerged && <span className="rounded bg-purple-100 px-1 text-[9px] text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">merged</span>}
        </div>
        {warning && (
          <p className="text-yellow-700 dark:text-yellow-400">⚠ {warning}</p>
        )}
      </div>
    );
  }

  if (entry.event_type === "git_branch_deleted") {
    return (
      <div className="mt-1 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] dark:border-slate-600 dark:bg-slate-900">
        <div className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
          <GitBranch className="h-3 w-3 shrink-0" />
          <span>Branch silindi: <code className="dark:text-slate-300">{String(meta.branch ?? "")}</code></span>
        </div>
      </div>
    );
  }

  return null;
}
