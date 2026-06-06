import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ChevronUp, GitBranch, GitCommit, GitMerge, GitPullRequest, Send, Wifi, WifiOff, X } from "lucide-react";
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
import { PRIORITY_DOT, cn } from "@/lib/utils";
import { resolveStateColor } from "@/lib/stateColor";
import { DiffViewer } from "@/components/diff/DiffViewer";
import { TicketCommits } from "@/components/git/TicketCommits";
import type {
  ActorSummary,
  ApiError,
  CommentResponse,
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

  const stateObj = board.workflow.states.find((s) => s.name === ticket.state);

  return (
    <section>
      <Link to={`/boards/${boardKey}`} className="crumb">
        <ArrowLeft className="h-[15px] w-[15px]" />
        {board.name}
      </Link>

      <div className="td-head">
        {/* LEFT: chip row + title */}
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-2.5">
            <span className="key-chip">{ticket.key}</span>
            <TypeChip type={ticket.type} />
            {ticket.agent_phase && (
              <span className="t-phase" title={ticket.agent_phase.message ?? undefined}>
                <i className="dot dot-sm animate-pulse" style={{ background: "var(--accent)" }} />
                {ticket.agent_phase.agent_id} · {ticket.agent_phase.phase}
              </span>
            )}
          </div>
          <h1 className="text-[24px] font-semibold leading-[1.25] tracking-[-0.015em] text-text-primary">
            {ticket.title}
          </h1>
        </div>

        {/* RIGHT: StateControl + Live pill + admin Sil */}
        <div className="flex shrink-0 items-center gap-2">
          {transitionOptions.length > 0 ? (
            <div className="relative" ref={moveMenuRef}>
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={() => setMoveMenuOpen((o) => !o)}
                disabled={transitionMutation.isPending}
                aria-haspopup="menu"
                aria-expanded={moveMenuOpen}
              >
                <StatePill state={ticket.state} stateObj={stateObj} />
                <ChevronDown
                  className={cn(
                    "h-3.5 w-3.5 transition-transform duration-fast ease-out",
                    moveMenuOpen && "rotate-180",
                  )}
                />
              </button>

              {moveMenuOpen && (
                <div role="menu" aria-label="Move to" className="menu">
                  <div className="menu-label">Move to →</div>
                  {transitionOptions.map(({ to, dotColor, requiresFields }) => (
                    <button
                      key={to}
                      type="button"
                      role="menuitem"
                      className="menu-item"
                      onClick={() => {
                        transitionMutation.mutate(to);
                        setMoveMenuOpen(false);
                      }}
                      disabled={transitionMutation.isPending}
                    >
                      <i className="dot dot-sm" style={{ background: dotColor }} />
                      <span className="mono">{to}</span>
                      {requiresFields && <span className="menu-hint">req fields</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <StatePill state={ticket.state} stateObj={stateObj} />
          )}

          <span
            className="status-pill"
            style={{
              fontSize: 11,
              padding: "4px 9px",
              color: isConnected ? "var(--success)" : isConnecting ? "var(--warning)" : "var(--danger)",
              background: isConnected ? "var(--success-soft)" : isConnecting ? "var(--warning-soft)" : "var(--danger-soft)",
            }}
            title={isConnected ? "Live updates active" : isConnecting ? "Connecting..." : "Disconnected"}
          >
            {isConnected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
            {isConnected ? "Live" : isConnecting ? "…" : "Off"}
          </span>

          {role === "admin" && (
            <button
              type="button"
              onClick={() => {
                setDeleteReason("");
                setDeleteError(null);
                setShowDeleteModal(true);
              }}
              className="btn-ghost btn-sm text-danger hover:bg-danger-soft hover:text-danger"
              data-testid="delete-ticket-button"
            >
              Sil
            </button>
          )}
        </div>
      </div>

      {/* Transition feedback (relocated from sidebar; keeps #field-<key> anchor jump) */}
      {(transitionError || successToastMessage) && (
        <div className="mb-4 space-y-2">
          {transitionError && <TransitionErrorBanner error={transitionError} />}
          {successToastMessage && (
            <SuccessToast
              message={successToastMessage}
              onDismiss={() => setSuccessToastMessage(null)}
            />
          )}
        </div>
      )}

      <div className="td-grid">
        <div className="td-col">
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

        <aside className="td-side">
          <div className="card" style={{ padding: 16 }}>
            <div className="side-row">
              <span className="side-label">Priority</span>
              <span className="side-val">
                <span className="inline-flex items-center gap-1.5 text-text-secondary">
                  <span className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[ticket.priority])} />
                  {ticket.priority}
                </span>
              </span>
            </div>

            <div className="side-row">
              <span className="side-label">Reporter</span>
              <span className="side-val">
                <ActorChip actor={ticket.reporter} />
              </span>
            </div>

            <div className="side-row">
              <span className="side-label">Assignee</span>
              <span className="side-val">
                <ActorChip actor={ticket.assignee} withAvatar />
              </span>
            </div>

            <div className="side-row">
              <span className="side-label">Labels</span>
              <span className="side-val">
                {ticket.labels.length === 0 ? (
                  <span className="text-text-muted">—</span>
                ) : (
                  <span className="flex flex-wrap justify-end gap-1">
                    {ticket.labels.map((l) => (
                      <span key={l} className="label-chip">{l}</span>
                    ))}
                  </span>
                )}
              </span>
            </div>

            <div className="side-row">
              <span className="side-label">Created</span>
              <span className="side-val mono text-text-muted" style={{ fontSize: 12 }}>
                {new Date(ticket.created_at).toLocaleString()}
              </span>
            </div>

            {ticket.branch_name && (() => {
              const defaultBranch = board.repository?.default_branch ?? null;
              const isSameBranch = ticket.branch_name === defaultBranch;
              const noRepo = !defaultBranch;

              let tooltip = "";
              if (noRepo) tooltip = "Repo bağlı değil";
              else if (isSameBranch) tooltip = "Default branch — kendine diff yok";

              const isDisabled = noRepo || isSameBranch;

              return (
                <div className="side-row">
                  <span className="side-label">Branch</span>
                  <span className="side-val">
                    <button
                      type="button"
                      aria-label={`View diff for branch ${ticket.branch_name}`}
                      title={tooltip || `${defaultBranch ?? "main"}...${ticket.branch_name}`}
                      disabled={isDisabled}
                      onClick={() => { if (!isDisabled) setShowBranchDiff(true); }}
                      className="branch-row mono"
                    >
                      <GitBranch className="h-[13px] w-[13px] shrink-0" />
                      {ticket.branch_name}
                    </button>
                  </span>
                </div>
              );
            })()}
          </div>
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

// ---------------------------------------------------------------------------
// Presentational primitives — reproduce ui_kit primitives.jsx with F1 tokens.
// ---------------------------------------------------------------------------

// State-colored pill (kit `.status-pill` / `StatePill`). Carries the
// `ticket-state-badge` testid + an inline `style` with the state hex so the
// workflow-state-color e2e (asserts rgb of the configured color) still passes.
function StatePill({ state, stateObj }: { state: string; stateObj: WorkflowState | undefined }) {
  const tone = resolveStateColor(stateObj);
  // Prefer the resolved hex (style.color); fall back to the F1 --state-<id> token.
  const color = (tone.style?.color as string) ?? `var(--state-${state})`;
  return (
    <span
      data-testid="ticket-state-badge"
      className="status-pill mono"
      style={{
        color,
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
        border: `1px solid color-mix(in srgb, ${color} 34%, transparent)`,
      }}
    >
      <i className="dot dot-sm" style={{ background: color }} />
      {state}
    </span>
  );
}

// Type chip (kit `.type-chip`) — color from the type→lane token map.
const TYPE_CHIP_COLOR: Record<string, string> = {
  feature: "var(--lane-emerald)",
  bug: "var(--lane-rose)",
  task: "var(--lane-sky)",
  epic: "var(--lane-violet)",
};
function TypeChip({ type }: { type: string }) {
  const c = TYPE_CHIP_COLOR[type] ?? "var(--text-muted)";
  return (
    <span
      className="type-chip"
      style={{ color: c, background: `color-mix(in srgb, ${c} 16%, transparent)` }}
    >
      {type}
    </span>
  );
}

// Avatar (kit `.avatar` / `.avatar.sm`) — mono initials, cyan tint.
function Avatar({ name, sm }: { name: string; sm?: boolean }) {
  const initials = name.replace(/^jarwis-/, "").slice(0, 2).toUpperCase() || "?";
  return (
    <span className={cn("avatar", sm && "sm")} title={name} aria-hidden>
      {initials}
    </span>
  );
}

// Maps an actor's role hint (e.g. "frontend_dev", "qa") to a label + role token.
const ROLE_TOKEN: Record<string, { label: string; color: string }> = {
  admin: { label: "admin", color: "var(--role-admin)" },
  pm: { label: "pm", color: "var(--role-pm)" },
  architect: { label: "arch", color: "var(--role-architect)" },
  backend_dev: { label: "be", color: "var(--role-backend)" },
  backend: { label: "be", color: "var(--role-backend)" },
  frontend_dev: { label: "fe", color: "var(--role-frontend)" },
  frontend: { label: "fe", color: "var(--role-frontend)" },
  reviewer: { label: "rev", color: "var(--role-reviewer)" },
  qa: { label: "qa", color: "var(--role-qa)" },
  orchestrator: { label: "orch", color: "var(--role-orchestrator)" },
};

// Role chip (kit `.role-chip`) — mono 11px pill, role-token colored.
function RoleChip({ roleHint }: { roleHint: string | null | undefined }) {
  const role = roleHint ? ROLE_TOKEN[roleHint] : undefined;
  if (!role) return null;
  return (
    <span
      className="role-chip"
      style={{ color: role.color, background: `color-mix(in srgb, ${role.color} 14%, transparent)` }}
    >
      {role.label}
    </span>
  );
}

function ActorChip({ actor, withAvatar }: { actor: ActorSummary | null | undefined; withAvatar?: boolean }) {
  if (!actor) return <span className="text-text-muted">—</span>;
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      {withAvatar && <Avatar name={actor.display_name} sm />}
      <span className="mono text-text-secondary" style={{ fontSize: 12 }}>{actor.display_name}</span>
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

function CommentCard({ c }: { c: CommentResponse }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = c.body.length > COLLAPSE_THRESHOLD;
  const displayBody = isLong && !expanded ? c.body.slice(0, COLLAPSE_THRESHOLD) + "\u2026" : c.body;

  return (
    <li className="activity-item">
      <Avatar name={c.author.display_name} sm />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="mono text-text-primary" style={{ fontSize: 12 }}>{c.author.display_name}</span>
          <RoleChip roleHint={c.author.agent_role_hint} />
          <span className="mono ml-auto text-text-muted" style={{ fontSize: 11 }}>
            {new Date(c.created_at).toLocaleString()}
            {c.edited_at && <span className="ml-1 italic">(düzenlendi)</span>}
          </span>
        </div>
        <div className="prose-sm" style={{ fontSize: 12.5 }}>
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
      </div>
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
    <section className="card" style={{ padding: 0 }}>
      {/* Tabs row (inset, kit `.tabs`) */}
      <div style={{ padding: "4px 16px 0" }}>
        <div className="tabs" role="tablist" aria-label="Aktivite filtresi">
          {FILTER_TABS.map((tab) => {
            const active = filter === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setFilter(tab.key)}
                className={cn("tab", active && "active")}
              >
                {tab.label}
                <span className="tab-count">{tab.count}</span>
                {active && <span className="underline" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Body */}
      <div className="field-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {/* Comments */}
        {(filter === "all" || filter === "comments") && (
          <div className="space-y-3.5">
            {commentsQuery.isLoading ? (
              <p className="text-xs text-text-muted">Yükleniyor…</p>
            ) : comments.length === 0 ? (
              filter === "comments" && <p className="text-xs text-text-muted">Henüz yorum yok.</p>
            ) : (
              <ol className="flex flex-col gap-3.5">
                {comments.map((c) => <CommentCard key={c.id} c={c} />)}
              </ol>
            )}
          </div>
        )}

        {/* History */}
        {(filter === "all" || filter === "history") && histOnly.length > 0 && (
          <ol className="flex flex-col gap-3.5">
            {histOnly.map((e) => (
              <li key={e.id} className="activity-item">
                <Avatar name={e.actor?.display_name ?? "system"} sm />
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="mono text-text-primary" style={{ fontSize: 12 }}>
                      {e.actor?.display_name ?? "system"}
                    </span>
                    <RoleChip roleHint={e.actor?.agent_role_hint} />
                    <span className="mono ml-auto text-text-muted" style={{ fontSize: 11 }}>
                      {new Date(e.created_at).toLocaleString()}
                    </span>
                  </div>
                  <div className="text-text-secondary" style={{ fontSize: 12.5, lineHeight: 1.5 }}>
                    <span className="font-medium text-text-primary">{EVENT_LABELS[e.event_type] ?? e.event_type}</span>
                    {e.field && <span className="ml-1 text-text-muted">({e.field})</span>}
                    {renderChange(e)}
                  </div>
                </div>
              </li>
            ))}
          </ol>
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

        {/* New comment composer — inline single-line row (kit `.composer`) */}
        <form onSubmit={submit} className="composer">
          <input
            className="input font-mono text-xs"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Write a comment… (Markdown supported)"
          />
          <button
            type="submit"
            className="btn-primary btn-sm shrink-0"
            disabled={addMut.isPending || body.trim().length === 0}
          >
            <Send className="h-3.5 w-3.5" />
            {addMut.isPending ? "Gönderiliyor…" : "Gönder"}
          </button>
        </form>
        {addMut.error && (
          <p className="text-xs text-danger">{(addMut.error as Error).message}</p>
        )}
      </div>
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
