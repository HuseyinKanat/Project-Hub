import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, ChevronDown, ChevronUp, GitBranch, GitCommit, GitMerge, GitPullRequest, MessageSquarePlus, Wifi, WifiOff, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useWebSocket } from "@/hooks/useWebSocket";
import { useBoardRole } from "@/hooks/useMe";

import { ApiRequestError, api } from "@/api/client";
import { useAuth } from "@/stores/auth";
import { FieldEditor } from "@/components/FieldEditor";
import { MarkdownFieldEditor } from "@/components/MarkdownFieldEditor";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { SuccessToast } from "@/components/SuccessToast";
import { PRIORITY_DOT, TYPE_BADGE, cn } from "@/lib/utils";
import { resolveStateColor } from "@/lib/stateColor";
import { DiffViewer } from "@/components/diff/DiffViewer";
import { TicketCommits } from "@/components/git/TicketCommits";
import type {
  ActorSummary,
  ApiError,
  HistoryEntry,
  TicketResponse,
  TicketUpdatePayload,
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
  const navigate = useNavigate();
  const role = useBoardRole(boardKey);

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

  const token = useAuth((s) => s.token) ?? "dev-token";
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
      window.dispatchEvent(new CustomEvent("notification:new"));
      if (message.ticket_key !== ticketKey) return;
      if (LIVE_EVENTS.has(message.type)) {
        void api.getTicket(ticketKey).then((updated) => {
          qc.setQueryData(["ticket", ticketKey], updated);
        });
        qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
        // G12: invalidate ticket-commits on any git event (silent refetch, scroll preserved)
        qc.invalidateQueries({ queryKey: ["ticket-commits", ticketKey] });
        if (message.type === "comment_added") {
          qc.invalidateQueries({ queryKey: ["ticket-comments", ticketKey] });
        }
      }
    },
  });

  const ticket = ticketQuery.data;
  const board = boardQuery.data;
  const allowedTransitions = useMemo(() => {
    if (!ticket || !board) return [];
    return board.workflow.transitions
      .filter((t) => t.from === ticket.state || t.from === "*")
      .map((t) => t.to);
  }, [ticket, board]);

  // Rich transition descriptors for the "Move to →" popover: target state,
  // its dot color (state.color hex if set, else the F1 --state-<name> token),
  // and whether the transition has required-field gates.
  const transitionOptions = useMemo(() => {
    if (!ticket || !board) return [];
    return board.workflow.transitions
      .filter((t) => t.from === ticket.state || t.from === "*")
      .map((t) => {
        const targetState = board.workflow.states.find((s) => s.name === t.to);
        const requiredFields = t.field_gates?.required_fields ?? [];
        const dotColor =
          targetState?.color && /^#[0-9a-fA-F]{6}$/.test(targetState.color)
            ? targetState.color
            : `var(--state-${t.to})`;
        return {
          to: t.to,
          dotColor,
          requiresFields: requiredFields.length > 0,
        };
      });
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
  const [successToastMessage, setSuccessToastMessage] = useState<string | null>(null);
  const transitionMutation = useMutation({
    mutationFn: (toState: string) => api.transitionTicket(ticketKey, toState),
    onSuccess: (updated, toState) => {
      setTransitionError(null);
      qc.setQueryData(["ticket", ticketKey], updated);
      qc.invalidateQueries({ queryKey: ["tickets", boardKey] });
      qc.invalidateQueries({ queryKey: ["ticket-history", ticketKey] });
      // Build success toast message, including required-field satisfaction if applicable
      const fromState = ticket?.state ?? "?";
      const activeTransition = board?.workflow.transitions.find(
        (t) => (t.from === fromState || t.from === "*") && t.to === toState,
      );
      const requiredFields = activeTransition?.field_gates?.required_fields ?? [];
      if (requiredFields.length > 0) {
        setSuccessToastMessage(
          `${fromState} → ${toState}: ${requiredFields.map((f) => `${f} ✓`).join(", ")}`,
        );
      } else {
        setSuccessToastMessage(`${fromState} → ${toState}`);
      }
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.body) {
        setTransitionError(err.body);
      } else {
        setTransitionError({ error: "unknown", message: (err as Error).message });
      }
    },
  });

  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [showBranchDiff, setShowBranchDiff] = useState(false);
  const [moveMenuOpen, setMoveMenuOpen] = useState(false);
  const moveMenuRef = useRef<HTMLDivElement>(null);

  // "Move to →" popover: close on click-outside + Escape.
  useEffect(() => {
    if (!moveMenuOpen) return;
    function handleClick(e: MouseEvent) {
      if (moveMenuRef.current && !moveMenuRef.current.contains(e.target as Node)) {
        setMoveMenuOpen(false);
      }
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMoveMenuOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [moveMenuOpen]);

  const deleteMutation = useMutation({
    mutationFn: ({ reason }: { reason: string }) => api.deleteTicket(ticketKey, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tickets", boardKey] });
      qc.removeQueries({ queryKey: ["ticket", ticketKey] });
      navigate(`/boards/${boardKey}`, { replace: true, state: { toast: `${ticketKey} silindi` } });
    },
    onError: (err: Error) => {
      setDeleteError(err.message);
    },
  });

  if (ticketQuery.isLoading || boardQuery.isLoading) {
    return <p className="text-sm text-text-muted">Yükleniyor…</p>;
  }
  if (ticketQuery.error) {
    return (
      <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
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
          className="inline-flex items-center gap-1 text-xs text-text-muted transition-colors hover:text-accent"
        >
          <ArrowLeft className="h-3 w-3" />
          {board.name}
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-sm text-accent">{ticket.key}</span>
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide",
              TYPE_BADGE[ticket.type] ?? "bg-raised text-text-secondary",
            )}
          >
            {ticket.type}
          </span>
          <h1 className="flex-1 text-2xl font-semibold tracking-tight text-text-primary">{ticket.title}</h1>
          <div
            className={cn(
              "flex items-center gap-1 rounded px-2 py-0.5 text-2xs",
              isConnected
                ? "bg-success-soft text-success"
                : isConnecting
                  ? "bg-warning-soft text-warning"
                  : "bg-danger-soft text-danger"
            )}
            title={isConnected ? "Live updates active" : isConnecting ? "Connecting..." : "Disconnected"}
          >
            {isConnected ? <Wifi className="h-2.5 w-2.5" /> : <WifiOff className="h-2.5 w-2.5" />}
            <span>{isConnected ? "Live" : isConnecting ? "…" : "Off"}</span>
          </div>
          {role === "admin" && (
            <button
              type="button"
              onClick={() => {
                setDeleteReason("");
                setDeleteError(null);
                setShowDeleteModal(true);
              }}
              className="btn-ghost text-danger hover:bg-danger-soft hover:text-danger"
              data-testid="delete-ticket-button"
            >
              Sil
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[1fr_18rem]">
        <div className="order-2 space-y-3 lg:order-1">
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

          <ActivitySection ticketKey={ticketKey} boardKey={boardKey} historyEntries={historyQuery.data ?? []} />
        </div>

        <aside className="order-1 space-y-3 lg:order-2">
          {/* Quick Edit Fields Summary */}
          <div className="card p-3 space-y-2">
            <h3 className="eyebrow">Alanlar</h3>
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
                      "w-full flex items-center justify-between rounded px-2 py-1.5 text-xs transition-colors duration-fast ease-out",
                      "hover:bg-raised",
                      isFilled ? "text-text-secondary" : "text-text-muted"
                    )}
                  >
                    <span className="flex items-center gap-1.5">
                      <span
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          isFilled ? "bg-success" : "bg-text-muted"
                        )}
                      />
                      {f.label}
                      {f.required && <span className="text-warning">*</span>}
                    </span>
                    <span className="text-2xs">
                      {isFilled ? "✓" : "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="card p-3 space-y-3">
            <div>
              <p className="eyebrow mb-1.5">State</p>
              {(() => {
                const stateObj = board.workflow.states.find((s) => s.name === ticket.state);
                const tone = resolveStateColor(stateObj);
                return (
                  <span
                    data-testid="ticket-state-badge"
                    className={cn(
                      "inline-flex w-full items-center justify-center rounded-md px-3 py-1.5 text-xs font-semibold ring-1",
                      tone.className,
                    )}
                    style={tone.style}
                  >
                    {ticket.state.replace(/_/g, " ")}
                  </span>
                );
              })()}
            </div>

            {transitionOptions.length > 0 && (
              <div className="space-y-2 border-t border-hairline pt-3">
                <div className="relative" ref={moveMenuRef}>
                  <button
                    type="button"
                    className="btn-secondary w-full justify-between text-xs"
                    onClick={() => setMoveMenuOpen((o) => !o)}
                    disabled={transitionMutation.isPending}
                    aria-haspopup="menu"
                    aria-expanded={moveMenuOpen}
                  >
                    <span>Move to →</span>
                    <ChevronDown
                      className={cn(
                        "h-3.5 w-3.5 transition-transform duration-fast ease-out",
                        moveMenuOpen && "rotate-180",
                      )}
                    />
                  </button>

                  {moveMenuOpen && (
                    <div
                      role="menu"
                      aria-label="Move to"
                      className="absolute right-0 top-[calc(100%+6px)] z-50 min-w-[200px] rounded-lg border border-hairline-cyan bg-raised p-1.5 shadow-lg"
                    >
                      <p className="eyebrow px-2 py-1">Move to →</p>
                      {transitionOptions.map(({ to, dotColor, requiresFields }) => (
                        <button
                          key={to}
                          type="button"
                          role="menuitem"
                          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-text-secondary transition-colors duration-fast ease-out hover:bg-accent-soft hover:text-text-primary"
                          onClick={() => {
                            transitionMutation.mutate(to);
                            setMoveMenuOpen(false);
                          }}
                          disabled={transitionMutation.isPending}
                        >
                          <span
                            className="h-1.5 w-1.5 shrink-0 rounded-full"
                            style={{ backgroundColor: dotColor }}
                          />
                          <span className="mono flex-1">{to.replace(/_/g, " ")}</span>
                          {requiresFields && (
                            <span className="text-2xs text-warning">req fields</span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {transitionError && <TransitionErrorBanner error={transitionError} />}
                {successToastMessage && (
                  <SuccessToast
                    message={successToastMessage}
                    onDismiss={() => setSuccessToastMessage(null)}
                  />
                )}
              </div>
            )}
          </div>

          <div className="card p-3 text-xs space-y-2 text-text-secondary">
            <Row label="Priority">
              <span className="inline-flex items-center gap-1.5">
                <span className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[ticket.priority])} />
                {ticket.priority}
              </span>
            </Row>
            <Row label="Reporter"><ActorChip actor={ticket.reporter} /></Row>
            <Row label="Assignee"><ActorChip actor={ticket.assignee} /></Row>
            <Row label="Labels">
              {ticket.labels.length === 0 ? (
                "—"
              ) : (
                <span className="flex flex-wrap gap-1">
                  {ticket.labels.map((l) => (
                    <span
                      key={l}
                      className="rounded-pill border border-hairline bg-raised px-2 py-0.5 text-2xs text-text-secondary"
                    >
                      {l}
                    </span>
                  ))}
                </span>
              )}
            </Row>
            <Row label="Created">{new Date(ticket.created_at).toLocaleString()}</Row>
            {ticket.branch_name && (() => {
              const defaultBranch = board.repository?.default_branch ?? null;
              const isSameBranch = ticket.branch_name === defaultBranch;
              const noRepo = !defaultBranch;

              let tooltip = "";
              if (noRepo) tooltip = "Repo bağlı değil";
              else if (isSameBranch) tooltip = "Default branch — kendine diff yok";

              const isDisabled = noRepo || isSameBranch;

              return (
                <Row label="Branch">
                  <button
                    type="button"
                    aria-label={`View diff for branch ${ticket.branch_name}`}
                    title={tooltip || `${defaultBranch ?? "main"}...${ticket.branch_name}`}
                    disabled={isDisabled}
                    onClick={() => { if (!isDisabled) setShowBranchDiff(true); }}
                    className={cn(
                      "inline-flex items-center gap-1 rounded border border-hairline-cyan bg-accent-soft px-1.5 py-0.5 font-mono text-2xs text-accent transition-colors duration-fast ease-out",
                      isDisabled
                        ? "opacity-60 cursor-not-allowed"
                        : "hover:bg-accent-active hover:text-text-on-accent cursor-pointer"
                    )}
                  >
                    <GitBranch className="h-3 w-3 shrink-0" />
                    {ticket.branch_name}
                  </button>
                </Row>
              );
            })()}
          </div>

          {ticket.agent_phase && (
            <div className="card p-3 space-y-1">
              <p className="eyebrow">Agent Phase</p>
              <div className="flex items-center gap-1.5 rounded bg-warning-soft px-2 py-1 text-xs text-warning ring-1 ring-hairline">
                <Activity className="h-3 w-3 animate-pulse" />
                <span>
                  {ticket.agent_phase.agent_id} · {ticket.agent_phase.phase}
                </span>
              </div>
              {ticket.agent_phase.message && (
                <p className="text-[11px] text-text-secondary">{ticket.agent_phase.message}</p>
              )}
            </div>
          )}

        </aside>
      </div>

      {/* Branch range diff modal — G12 AC5 */}
      {showBranchDiff && ticket.branch_name && board.repository?.default_branch && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center pt-16 px-4 pb-8 overflow-y-auto"
          style={{ background: "var(--bg-overlay)", backdropFilter: "blur(4px)" }}
          onClick={() => setShowBranchDiff(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="branch-diff-modal-title"
          onKeyDown={(e) => { if (e.key === "Escape") setShowBranchDiff(false); }}
        >
          <div
            className="card w-full max-w-4xl space-y-4 p-6"
            style={{ boxShadow: "var(--shadow-glass)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2">
              <h2 id="branch-diff-modal-title" className="mono truncate text-sm font-semibold text-text-primary">
                {board.repository.default_branch}...{ticket.branch_name}
              </h2>
              <button
                type="button"
                aria-label="Close diff"
                onClick={() => setShowBranchDiff(false)}
                className="rounded p-1 text-text-muted transition-colors hover:bg-raised hover:text-text-primary"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <DiffViewer
              fetch={{
                kind: "range",
                boardKey,
                base: board.repository.default_branch,
                head: ticket.branch_name,
              }}
            />
          </div>
        </div>
      )}

      {showDeleteModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: "var(--bg-overlay)", backdropFilter: "blur(4px)" }}
          onClick={() => setShowDeleteModal(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-modal-title"
        >
          <div
            className="card w-full max-w-md space-y-4 p-6"
            style={{ boxShadow: "var(--shadow-glass)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="delete-modal-title" className="text-lg font-semibold text-text-primary">
              {ticketKey} silinsin mi?
            </h2>
            <p className="text-sm text-text-secondary">{ticket.title}</p>
            <textarea
              value={deleteReason}
              onChange={(e) => setDeleteReason(e.target.value)}
              placeholder="Silme sebebi (zorunlu)"
              className="input w-full"
              rows={3}
              autoFocus
              data-testid="delete-reason-input"
              aria-label="Silme sebebi"
            />
            {deleteError && (
              <p className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger" role="alert">
                {deleteError}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDeleteModal(false)}
                className="btn-ghost"
              >
                İptal
              </button>
              <button
                type="button"
                onClick={() => {
                  setDeleteError(null);
                  deleteMutation.mutate({ reason: deleteReason || "Deleted via UI" });
                }}
                disabled={deleteMutation.isPending}
                className="btn-primary"
                style={{ backgroundColor: "var(--danger)", color: "var(--text-on-accent)", boxShadow: "none" }}
                data-testid="confirm-delete-button"
              >
                {deleteMutation.isPending ? "Siliniyor…" : "Sil"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="text-text-muted">{label}</span>
      <span className="text-right text-text-primary">{children}</span>
    </div>
  );
}

// Maps an actor's role hint (e.g. "frontend_dev", "qa") to a label + a STATIC
// role-token class. Static strings keep Tailwind JIT from purging them (a
// dynamic `text-role-${x}` would not be scanned).
const ROLE_TOKEN: Record<string, { label: string; className: string }> = {
  admin: { label: "admin", className: "text-role-admin" },
  pm: { label: "pm", className: "text-role-pm" },
  architect: { label: "arch", className: "text-role-architect" },
  backend_dev: { label: "be", className: "text-role-backend" },
  backend: { label: "be", className: "text-role-backend" },
  frontend_dev: { label: "fe", className: "text-role-frontend" },
  frontend: { label: "fe", className: "text-role-frontend" },
  reviewer: { label: "rev", className: "text-role-reviewer" },
  qa: { label: "qa", className: "text-role-qa" },
  orchestrator: { label: "orch", className: "text-role-orchestrator" },
};

function ActorChip({ actor }: { actor: ActorSummary | null | undefined }) {
  if (!actor) return <span className="text-text-muted">—</span>;
  const initial = actor.display_name.replace(/^jarwis-/, "").charAt(0).toUpperCase() || "?";
  const role = actor.agent_role_hint ? ROLE_TOKEN[actor.agent_role_hint] : undefined;
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      <span
        className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-raised text-[9px] font-semibold text-text-secondary ring-1 ring-hairline"
        aria-hidden
      >
        {initial}
      </span>
      <span className="mono text-text-secondary">{actor.display_name}</span>
      {role && (
        <span
          className={cn(
            "rounded-pill border border-hairline bg-raised px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide",
            role.className,
          )}
        >
          {role.label}
        </span>
      )}
    </span>
  );
}

function TransitionErrorBanner({ error }: { error: ApiError }) {
  const cls = "rounded-md bg-danger-soft px-3 py-2 text-xs text-danger";
  if (error.error === "field_gate_not_met") {
    return (
      <div className={cls} role="alert">
        <p className="font-medium">Geçiş için eksik alan(lar):</p>
        <ul className="list-disc pl-4">
          {error.missing_fields?.map((f) => (
            <li key={f}>
              <a
                href={`#field-${f}`}
                onClick={(e) => {
                  e.preventDefault();
                  const el = document.getElementById(`field-${f}`);
                  el?.scrollIntoView({ behavior: "smooth", block: "center" });
                  el?.focus();
                }}
                className="rounded underline hover:opacity-80"
              >
                {f}
              </a>
            </li>
          ))}
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

const COLLAPSE_THRESHOLD = 300;

function CommentCard({ c }: { c: { id: string; author: { display_name: string }; created_at: string; edited_at?: string | null; body: string } }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = c.body.length > COLLAPSE_THRESHOLD;
  const displayBody = isLong && !expanded ? c.body.slice(0, COLLAPSE_THRESHOLD) + "\u2026" : c.body;

  return (
    <li className="rounded-lg border border-hairline bg-inset p-3 shadow-sm">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] text-text-muted">
          <span className="font-semibold text-text-primary">{c.author.display_name}</span>
          <span>·</span>
          <span className="mono">{new Date(c.created_at).toLocaleString()}</span>
          {c.edited_at && <span className="italic">(düzenlendi)</span>}
        </div>
      </div>
      <div className="prose-sm">
        <MarkdownRenderer content={displayBody} />
      </div>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 flex items-center gap-0.5 text-[11px] text-accent hover:text-accent-hover hover:underline"
        >
          {expanded ? <><ChevronUp className="h-3 w-3" /> Daha az</> : <><ChevronDown className="h-3 w-3" /> Daha fazla</>}
        </button>
      )}
    </li>
  );
}

type ActivityFilter = "all" | "comments" | "history" | "git";

function ActivitySection({ ticketKey, boardKey, historyEntries }: { ticketKey: string; boardKey: string; historyEntries: HistoryEntry[] }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const [filter, setFilter] = useState<ActivityFilter>("all");

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

  // G12: fetch ticket commits for Git tab (count badge + TicketCommits rendering)
  const ticketCommitsQuery = useQuery({
    queryKey: ["ticket-commits", ticketKey],
    queryFn: () => api.git.getTicketCommits(ticketKey),
    staleTime: 30_000,
    retry: false,
  });

  const comments = commentsQuery.data ?? [];
  const gitEntries = historyEntries.filter((e) => e.event_type.startsWith("git_"));
  const histOnly = historyEntries.filter((e) => !e.event_type.startsWith("git_"));
  const commitCount = ticketCommitsQuery.data?.commits.length ?? 0;

  const FILTER_TABS: { key: ActivityFilter; label: string; count: number }[] = [
    { key: "all", label: "Tümü", count: comments.length + historyEntries.length },
    { key: "comments", label: "Yorumlar", count: comments.length },
    { key: "history", label: "Geçmiş", count: histOnly.length },
    // Git tab count = commits (rich rows); history badges shown separately below
    { key: "git", label: "Git", count: commitCount },
  ];

  const EVENT_LABELS: Record<string, string> = {
    state_changed: "Durum değişti",
    assigned: "Atandı",
    unassigned: "Atama kaldırıldı",
    claimed: "Claim edildi",
    released: "Release edildi",
    field_changed: "Alan güncellendi",
    phase_updated: "Faz güncellendi",
    agent_phase_updated: "Agent fazı güncellendi",
    comment_added: "Yorum eklendi",
    created: "Oluşturuldu",
  };

  return (
    <section className="card p-3 space-y-3">
      {/* Filter tabs — accent-underline idiom */}
      <div className="flex items-center gap-1 border-b border-hairline" role="tablist" aria-label="Aktivite filtresi">
        {FILTER_TABS.map((tab) => {
          const active = filter === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setFilter(tab.key)}
              className={cn(
                "relative flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors duration-fast ease-out",
                active ? "text-accent" : "text-text-secondary hover:text-text-primary"
              )}
            >
              {tab.label}
              <span className={cn(
                "rounded-full border px-1.5 py-0.5 text-[10px] font-semibold",
                active
                  ? "border-hairline-cyan bg-accent-soft text-accent"
                  : "border-hairline bg-raised text-text-muted"
              )}>{tab.count}</span>
              {active && (
                <span className="pointer-events-none absolute inset-x-2 -bottom-px h-0.5 rounded bg-accent shadow-glow-cyan-sm" />
              )}
            </button>
          );
        })}
      </div>

      {/* Comments */}
      {(filter === "all" || filter === "comments") && (
        <div className="space-y-2">
          {filter === "all" && comments.length > 0 && (
            <p className="flex items-center gap-1 text-xs font-semibold text-text-secondary">
              <MessageSquarePlus className="h-3.5 w-3.5" /> Yorumlar
            </p>
          )}
          {commentsQuery.isLoading ? (
            <p className="text-xs text-text-muted">Yükleniyor…</p>
          ) : comments.length === 0 ? (
            filter === "comments" && <p className="text-xs text-text-muted">Henüz yorum yok.</p>
          ) : (
            <ol className="space-y-2">
              {comments.map((c) => <CommentCard key={c.id} c={c} />)}
            </ol>
          )}
        </div>
      )}

      {/* History */}
      {(filter === "all" || filter === "history") && histOnly.length > 0 && (
        <div className="space-y-1.5">
          {filter === "all" && (
            <p className="flex items-center gap-1 text-xs font-semibold text-text-secondary">
              <Activity className="h-3.5 w-3.5" /> Geçmiş
            </p>
          )}
          <ol className="space-y-1 text-xs">
            {histOnly.map((e) => (
              <li key={e.id} className="flex items-start gap-2 rounded-md px-2 py-1.5 transition-colors duration-fast ease-out hover:bg-raised">
                <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-text-muted" />
                <div className="flex-1 min-w-0">
                  <span className="font-medium text-text-primary">{EVENT_LABELS[e.event_type] ?? e.event_type}</span>
                  {e.field && <span className="ml-1 text-text-muted">({e.field})</span>}
                  {renderChange(e)}
                  <span className="mono ml-2 text-[10px] text-text-muted">
                    {e.actor ? e.actor.display_name + " · " : ""}{new Date(e.created_at).toLocaleString()}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Git section — G12: TicketCommits (rich view) above GitEventBadge (timeline badges) */}
      {(filter === "all" || filter === "git") && (
        <div className="space-y-2">
          {filter === "all" && (
            <p className="flex items-center gap-1 text-xs font-semibold text-text-secondary">
              <GitBranch className="h-3.5 w-3.5" /> Git
            </p>
          )}
          {/* Commit list (primary actor) — always shown in git/all filter */}
          <TicketCommits ticketKey={ticketKey} boardKey={boardKey} />

          {/* History git badges (timeline feed) — shown if any exist */}
          {gitEntries.length > 0 && (
            <div className="space-y-1">
              <p className="eyebrow">Git olayları</p>
              <ol className="space-y-1">
                {gitEntries.map((e) => <li key={e.id}><GitEventBadge entry={e} /></li>)}
              </ol>
            </div>
          )}

          {/* Empty state: both sources empty */}
          {filter === "git" && commitCount === 0 && gitEntries.length === 0 && !ticketCommitsQuery.isLoading && (
            <p className="text-xs text-text-muted">Henüz git aktivitesi yok.</p>
          )}
        </div>
      )}

      {/* New comment form */}
      <form onSubmit={submit} className="space-y-2 border-t border-hairline pt-3">
        <textarea
          className="input font-mono text-xs"
          rows={3}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Markdown destekli yorum…"
        />
        {addMut.error && (
          <p className="text-xs text-danger">{(addMut.error as Error).message}</p>
        )}
        <div className="flex justify-end">
          <button type="submit" className="btn-primary text-xs" disabled={addMut.isPending || body.trim().length === 0}>
            {addMut.isPending ? "Gönderiliyor…" : "Gönder"}
          </button>
        </div>
      </form>
    </section>
  );
}


const LONG_FIELD_THRESHOLD = 60;

function renderChange(e: HistoryEntry): React.ReactNode {
  if (e.event_type === "field_changed") {
    const oldStr = e.old_value == null ? "—" : String(e.old_value);
    const newStr = e.new_value == null ? "—" : String(e.new_value);
    const isLong = oldStr.length > LONG_FIELD_THRESHOLD || newStr.length > LONG_FIELD_THRESHOLD;

    if (isLong) {
      return (
        <span className="ml-1 italic text-text-muted">
          (önceki → yeni, {newStr.length} karakter)
        </span>
      );
    }
    return (
      <span className="ml-1 text-text-secondary">
        :{" "}
        <code className="mono rounded bg-inset px-1 py-0.5 text-[10px] text-text-muted line-through">{oldStr}</code>
        {" "}→{" "}
        <code className="mono rounded bg-inset px-1 py-0.5 text-[10px] text-text-primary">{newStr}</code>
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
      <div className="mt-1 space-y-0.5 rounded border border-hairline bg-inset px-2 py-1.5 text-[11px]">
        <div className="flex items-center gap-1.5 font-medium text-text-primary">
          <GitCommit className="h-3 w-3 shrink-0" />
          {url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" className="font-mono text-accent hover:text-accent-hover hover:underline">{sha}</a>
          ) : (
            <span className="font-mono">{sha}</span>
          )}
          {!isConventional && (
            <span className="rounded bg-warning-soft px-1 text-[9px] text-warning">non-conventional</span>
          )}
        </div>
        <p className="line-clamp-2 text-text-secondary">{msg}</p>
        <p className="text-text-muted">{author}{branch ? ` · ${branch}` : ""}</p>
      </div>
    );
  }

  if (entry.event_type === "git_commit_invalid_format") {
    return (
      <div className="mt-1 rounded border border-hairline bg-warning-soft px-2 py-1 text-[11px] text-warning">
        ⚠ Non-conventional commit {meta.sha_short} — beklenen: <code className="mono">feat(PH-XX): ...</code>
      </div>
    );
  }

  if (entry.event_type === "git_pr_linked" || entry.event_type === "git_pr_merged" || entry.event_type === "git_pr_closed") {
    const isMerged = entry.event_type === "git_pr_merged";
    const icon = isMerged
      ? <GitMerge className="h-3 w-3 shrink-0 text-lane-violet" />
      : <GitPullRequest className="h-3 w-3 shrink-0 text-info" />;
    const warning = meta.warning ? String(meta.warning) : null;
    return (
      <div className="mt-1 space-y-0.5 rounded border border-hairline bg-inset px-2 py-1 text-[11px]">
        <div className="flex items-center gap-1.5 text-text-primary">
          {icon}
          {meta.pr_url ? (
            <a href={String(meta.pr_url)} target="_blank" rel="noopener noreferrer" className="text-accent hover:text-accent-hover hover:underline">
              #{meta.pr_number} {meta.pr_title}
            </a>
          ) : (
            <span>#{meta.pr_number} {meta.pr_title}</span>
          )}
          {isMerged && <span className="rounded bg-accent-soft px-1 text-[9px] text-lane-violet">merged</span>}
        </div>
        {warning && (
          <p className="text-warning">⚠ {warning}</p>
        )}
      </div>
    );
  }

  if (entry.event_type === "git_branch_deleted") {
    return (
      <div className="mt-1 rounded border border-hairline bg-inset px-2 py-1 text-[11px]">
        <div className="flex items-center gap-1.5 text-text-muted">
          <GitBranch className="h-3 w-3 shrink-0" />
          <span>Branch silindi: <code className="mono text-text-secondary">{String(meta.branch ?? "")}</code></span>
        </div>
      </div>
    );
  }

  return null;
}
