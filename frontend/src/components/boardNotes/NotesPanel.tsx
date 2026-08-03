/**
 * NotesPanel.tsx — PH-336 (P6a): the board-scoped "Notes / Guardrails" panel
 * rendered inside the BoardSettings `notes` tab.
 *
 * The round-1 "recurring-mistake / warning notes" surface. A note is just
 * `body` + author + timestamp + `board_id` (NO severity/tag — cut in round 1;
 * NO trigger rules — P7; NO dispatch auto-injection — P6b). Humans WRITE here;
 * agents PULL the same notes read-only via the MCP `get_board_notes` tool, so a
 * board's recurring pitfalls travel into every agent's context. This is a
 * net-new board-scoped, DB-persisted, MCP-queryable store — explicitly NOT a
 * render/parse/mirror of CLAUDE.md.
 *
 * Auth: membership-gated for ALL ops (list/create/delete) — any board member
 * may add or delete (mirrors the backend `require_board_member` gate). Unknown
 * board -> 404, non-member -> 403, blank body -> 422; every failure surfaces
 * INLINE (never a crash), and a failed create PRESERVES the typed body for
 * retry (UC E1 — `onError` deliberately does not clear the textarea).
 *
 * Design system: Cyan-on-Black ProjectHub tokens only (`.card`, `.input`,
 * `.btn-primary/.btn-ghost`, semantic `text-success/danger/warning`,
 * `bg-*-soft`, `border-hairline*`, `mono`) — theme-aware for free, mirrors
 * SonarSetupSection / BoardSettings.
 */
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  StickyNote,
  Trash2,
  Loader2,
  AlertCircle,
  Plus,
} from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import type { BoardNote } from "@/types/api";

/**
 * Relative "3m ago" timestamp from an ISO string (no new dep; mirrors
 * SonarSetupSection.relativeTime). Falls back to the locale string for an
 * unparseable value, em-dash for a missing one.
 */
function relativeTime(iso: string): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const sec = Math.round((Date.now() - then) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (sec < 60) return rtf.format(-sec, "second");
  if (sec < 3600) return rtf.format(-Math.round(sec / 60), "minute");
  if (sec < 86400) return rtf.format(-Math.round(sec / 3600), "hour");
  return rtf.format(-Math.round(sec / 86400), "day");
}

/**
 * Map a create/delete error to a friendly inline message. 403 (non-member) and
 * 422 (blank body) get honest copy; any other `ApiRequestError`/`Error` shows
 * its own message. Never returns null for a real error (never a blank state).
 */
function noteErrorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (err.status === 403) return "You must be a board member to edit notes.";
    if (err.status === 404) return "This board no longer exists.";
    if (err.status === 422) return "A note cannot be empty.";
    return err.message || "Something went wrong.";
  }
  if (err instanceof Error) return err.message || "Something went wrong.";
  return "Something went wrong.";
}

/** One note row: body + author + timestamp + an inline-confirm delete control. */
function NoteItem({
  note,
  confirming,
  deleting,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: Readonly<{
  note: BoardNote;
  confirming: boolean;
  deleting: boolean;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}>) {
  return (
    <li className="card space-y-3 p-4" data-testid="board-note">
      <p
        className="whitespace-pre-wrap break-words text-sm text-text-primary"
        data-testid="board-note-body"
      >
        {note.body}
      </p>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-hairline pt-2 text-xs text-text-muted">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="font-medium text-text-secondary">
            {note.created_by_name ?? "unknown"}
          </span>
          <span aria-hidden="true">·</span>
          <time
            dateTime={note.created_at}
            title={new Date(note.created_at).toLocaleString()}
          >
            {relativeTime(note.created_at)}
          </time>
        </span>

        {confirming ? (
          <span className="flex items-center gap-2">
            <span className="text-warning">Delete this note?</span>
            <button
              type="button"
              onClick={onConfirmDelete}
              disabled={deleting}
              className="btn-primary inline-flex items-center gap-1.5 text-xs"
              data-testid="board-note-delete-confirm"
            >
              {deleting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Delete
            </button>
            <button
              type="button"
              onClick={onCancelDelete}
              disabled={deleting}
              className="btn-ghost text-xs"
              data-testid="board-note-delete-cancel"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={onAskDelete}
            className="btn-ghost inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-danger"
            aria-label="Delete note"
            data-testid="board-note-delete"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            Delete
          </button>
        )}
      </div>
    </li>
  );
}

export function NotesPanel({
  boardKey,
  enabled,
}: Readonly<{
  /** Board KEY or UUID — the backend `get_board` resolves both. */
  boardKey: string;
  /** Whether the tab is active — gates the list query (lazy fetch). */
  enabled: boolean;
}>) {
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const notesQuery = useQuery({
    queryKey: ["board-notes", boardKey],
    queryFn: () => api.listBoardNotes(boardKey),
    enabled: Boolean(boardKey) && enabled,
    staleTime: 15_000,
  });

  const createMutation = useMutation({
    mutationFn: (text: string) => api.createBoardNote(boardKey, { body: text }),
    onSuccess: () => {
      // Golden path: clear the input + refetch so the new note lands on top
      // (backend orders created_at DESC).
      setBody("");
      setCreateError(null);
      void queryClient.invalidateQueries({ queryKey: ["board-notes", boardKey] });
    },
    onError: (err) => {
      // UC E1: surface the failure inline AND KEEP the typed body for retry —
      // deliberately do NOT touch `body` here.
      setCreateError(noteErrorMessage(err));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (noteId: string) => api.deleteBoardNote(boardKey, noteId),
    onSuccess: () => {
      setConfirmId(null);
      setDeleteError(null);
      void queryClient.invalidateQueries({ queryKey: ["board-notes", boardKey] });
    },
    onError: (err) => {
      setDeleteError(noteErrorMessage(err));
    },
  });

  const trimmed = body.trim();
  const canSubmit = trimmed.length > 0 && !createMutation.isPending;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    // Client guard mirrors the backend non-blank rule so the common case never
    // round-trips a 422; the backend validator is still the source of truth.
    if (!trimmed) {
      setCreateError("A note cannot be empty.");
      return;
    }
    setCreateError(null);
    createMutation.mutate(trimmed);
  };

  const notes = notesQuery.data?.notes ?? [];

  return (
    <div className="space-y-6" data-testid="notes-panel-root">
      {/* Header + intent. */}
      <section className="card space-y-2 p-6">
        <div className="flex items-center gap-2">
          <StickyNote className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2 className="text-lg font-semibold text-text-primary">
            Notes / Guardrails
          </h2>
        </div>
        <p className="text-sm text-text-secondary">
          Board-scoped reminders and recurring-mistake warnings. Anyone on the
          board can add or remove a note; agents read them read-only over MCP
          (<code className="mono">get_board_notes</code>) so a board's pitfalls
          travel into every run.
        </p>

        {/* Add form. */}
        <form
          onSubmit={handleSubmit}
          className="space-y-2 pt-2"
          aria-label="Add a board note"
        >
          <label htmlFor="board-note-body" className="sr-only">
            New note
          </label>
          <textarea
            id="board-note-body"
            className="input min-h-[5rem] resize-y"
            placeholder="e.g. Always run migrations with PGOPTIONS lock_timeout — a bare upgrade hangs silently."
            value={body}
            onChange={(e) => setBody(e.target.value)}
            aria-describedby="board-note-hint"
            data-testid="board-note-input"
          />
          <div className="flex items-center justify-between gap-3">
            <p id="board-note-hint" className="text-xs text-text-muted">
              A note is free text — no severity or tags in round 1.
            </p>
            <button
              type="submit"
              className="btn-primary inline-flex items-center gap-2 text-sm"
              disabled={!canSubmit}
              data-testid="board-note-submit"
            >
              {createMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plus className="h-4 w-4" aria-hidden="true" />
              )}
              Add note
            </button>
          </div>

          {/* Inline create error (UC E1 — the body above is preserved). */}
          {createError && (
            <div
              className="flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
              role="alert"
              data-testid="board-note-create-error"
            >
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{createError}</span>
            </div>
          )}
        </form>
      </section>

      {/* Delete error (row-level failure surfaces here, panel stays usable). */}
      {deleteError && (
        <div
          className="flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
          role="alert"
          data-testid="board-note-delete-error"
        >
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{deleteError}</span>
        </div>
      )}

      {/* List states: loading -> error+retry -> empty -> notes. */}
      {notesQuery.isLoading && (
        <div
          className="card flex items-center gap-2 p-6 text-sm text-text-muted"
          data-testid="board-notes-loading"
        >
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading notes…
        </div>
      )}

      {notesQuery.isError && (
        <div
          className="card flex items-center gap-2 border-danger/40 bg-danger-soft p-6 text-sm text-danger"
          role="alert"
          data-testid="board-notes-error"
        >
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          {noteErrorMessage(notesQuery.error)}
          <button
            type="button"
            className="btn-ghost ml-2 text-xs"
            onClick={() => void notesQuery.refetch()}
          >
            Retry
          </button>
        </div>
      )}

      {notesQuery.isSuccess && notes.length === 0 && (
        <div
          className="card flex flex-col items-center gap-1 p-8 text-center text-sm text-text-muted"
          data-testid="board-notes-empty"
        >
          <StickyNote className="h-6 w-6 text-text-muted" aria-hidden="true" />
          <span>No notes yet — add the first guardrail above.</span>
        </div>
      )}

      {notesQuery.isSuccess && notes.length > 0 && (
        <ul className="space-y-3" data-testid="board-notes-list">
          {notes.map((note) => (
            <NoteItem
              key={note.id}
              note={note}
              confirming={confirmId === note.id}
              deleting={deleteMutation.isPending && confirmId === note.id}
              onAskDelete={() => {
                setDeleteError(null);
                setConfirmId(note.id);
              }}
              onCancelDelete={() => setConfirmId(null)}
              onConfirmDelete={() => deleteMutation.mutate(note.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
