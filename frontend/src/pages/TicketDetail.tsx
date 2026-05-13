import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, MessageSquarePlus } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

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
        "Mimari kararlar, etkilenen modüller, veri akışı, riskler. to_do → in_progress için zorunlu.",
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
        "Mimari kararlar, etkilenen modüller, veri akışı, riskler. to_do → in_progress için zorunlu.",
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
      description: "Root cause hipotezi + fix yaklaşımı. to_do → in_progress için zorunlu.",
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
    return <p className="text-sm text-slate-500">Yükleniyor…</p>;
  }
  if (ticketQuery.error) {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
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
          className="inline-flex items-center gap-1 text-xs text-slate-500 hover:underline"
        >
          <ArrowLeft className="h-3 w-3" />
          {board.name}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-sm text-slate-500">{ticket.key}</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
              TYPE_BADGE[ticket.type] ?? "bg-slate-100 text-slate-700",
            )}
          >
            {ticket.type}
          </span>
          <h1 className="flex-1 text-2xl font-semibold tracking-tight">{ticket.title}</h1>
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
            <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
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
                      // Scroll to and focus the field editor in main content
                      const el = document.getElementById(`field-${f.key as string}`);
                      el?.scrollIntoView({ behavior: "smooth", block: "center" });
                      el?.focus();
                    }}
                    className={cn(
                      "w-full flex items-center justify-between rounded px-2 py-1.5 text-xs transition-colors",
                      "hover:bg-slate-100",
                      isFilled ? "text-slate-700" : "text-slate-400"
                    )}
                  >
                    <span className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          isFilled ? "bg-green-500" : "bg-slate-300"
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
              <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
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
                <p className="text-xs text-slate-500">Geçiş</p>
                <div className="flex flex-wrap gap-1">
                  {allowedTransitions.map((to) => (
                    <button
                      key={to}
                      type="button"
                      className="btn-ghost text-xs ring-1 ring-slate-200"
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

          <div className="card p-3 text-xs space-y-2">
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
                      className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600"
                    >
                      {l}
                    </span>
                  ))}
                </span>
              )}
            </Row>
            <Row label="Created">{new Date(ticket.created_at).toLocaleString()}</Row>
          </div>

          {ticket.agent_phase && (
            <div className="card p-3 space-y-1">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Agent Phase
              </p>
              <div className="flex items-center gap-1.5 rounded bg-yellow-50 px-2 py-1 text-xs text-yellow-800 ring-1 ring-yellow-200">
                <Activity className="h-3 w-3 animate-pulse" />
                <span>
                  {ticket.agent_phase.agent_id} · {ticket.agent_phase.phase}
                </span>
              </div>
              {ticket.agent_phase.message && (
                <p className="text-[11px] text-slate-600">{ticket.agent_phase.message}</p>
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
      <span className="text-slate-500">{label}</span>
      <span className="text-right text-slate-800">{children}</span>
    </div>
  );
}

function TransitionErrorBanner({ error }: { error: ApiError }) {
  if (error.error === "field_gate_not_met") {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
        <p className="font-medium">Geçiş için eksik alan(lar):</p>
        <ul className="list-disc pl-4">
          {error.missing_fields?.map((f) => <li key={f}>{f}</li>)}
        </ul>
        <p className="mt-1 text-[11px] text-red-600">
          ({error.transition}) — alanı doldurup tekrar dene.
        </p>
      </div>
    );
  }
  if (error.error === "invalid_transition") {
    return (
      <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
        Geçersiz geçiş: {error.from_state} → {error.to_state}. İzin verilen:{" "}
        {error.allowed?.join(", ") ?? "—"}
      </div>
    );
  }
  return (
    <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
      {error.message ?? error.error}
    </div>
  );
}

function CommentsBlock({ ticketKey }: { ticketKey: string }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const addMut = useMutation({
    mutationFn: () => api.addComment(ticketKey, body),
    onSuccess: () => {
      setBody("");
      qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
    },
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    await addMut.mutateAsync();
  }

  return (
    <section className="card p-3 space-y-2">
      <h3 className="flex items-center gap-1 text-sm font-semibold text-slate-800">
        <MessageSquarePlus className="h-4 w-4" /> Yorum ekle
      </h3>
      <form onSubmit={submit} className="space-y-2">
        <textarea
          className="input font-mono text-xs"
          rows={3}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Markdown destekli yorum…"
        />
        {addMut.error && (
          <p className="text-xs text-red-700">{(addMut.error as Error).message}</p>
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
      <h3 className="text-xs font-medium uppercase tracking-wide text-slate-500">
        Activity ({entries.length})
      </h3>
      {entries.length === 0 ? (
        <p className="text-xs text-slate-500">Henüz aktivite yok.</p>
      ) : (
        <ol className="space-y-1.5 text-xs">
          {entries.map((e) => (
            <li key={e.id} className="border-l-2 border-slate-200 pl-2">
              <div className="text-slate-500">
                {new Date(e.created_at).toLocaleString()}
                {e.actor && <span className="ml-1">· {e.actor.display_name}</span>}
              </div>
              <div className="text-slate-800">
                <code className="text-[10px]">{e.event_type}</code>
                {e.field && <span className="ml-1 text-slate-600">({e.field})</span>}
                {renderChange(e)}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function renderChange(e: HistoryEntry): React.ReactNode {
  if (e.old_value === undefined && e.new_value === undefined) return null;
  if (e.event_type === "field_changed") {
    return (
      <span className="ml-1 text-slate-600">
        : <code className="text-[10px]">{JSON.stringify(e.old_value)}</code> →{" "}
        <code className="text-[10px]">{JSON.stringify(e.new_value)}</code>
      </span>
    );
  }
  if (e.new_value && typeof e.new_value === "object") return null;
  return null;
}
