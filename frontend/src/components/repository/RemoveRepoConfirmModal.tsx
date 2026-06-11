/**
 * RemoveRepoConfirmModal — C5 (PH-225)
 *
 * Per-repo remove confirm for the multi-repo settings list. Lighter than
 * DetachConfirmModal (which makes the user retype the BOARD key to wipe THE
 * single repo): removing one repo of N is reversible-ish (re-add) and the
 * backend auto-promotes the oldest remaining repo when the primary is removed,
 * so a plain "Remove <name>?" confirm is the right weight (architect's call).
 *
 * Behaviour:
 *  - DELETE /api/boards/{key}/repositories/{slug} (admin auth).
 *  - On success → caller-supplied onRemoved() (refetch list + detect).
 *  - 403 (non-admin) → inline 'admin yetkisi gerekli', NO crash (PH-224 trap).
 *  - When removing the primary while others remain, warn that the oldest
 *    remaining repo will become primary (mirrors backend auto-promotion).
 *
 * A11y:
 *  - role="dialog" aria-modal, ESC closes, focus the confirm button on open.
 *  - Click-outside dismiss surface is a real <button> (S6847/S6848).
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import { X, AlertTriangle } from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import type { RepositoryResponse } from "@/types/git";

interface RemoveRepoConfirmModalProps {
  boardKey: string;
  repo: RepositoryResponse;
  /** true when ≥2 repos remain AND this repo is primary (auto-promotion warning) */
  willAutoPromote: boolean;
  onClose: () => void;
  /** called after a successful remove so the parent can refetch list + detect */
  onRemoved: () => void;
}

export function RemoveRepoConfirmModal({
  boardKey,
  repo,
  willAutoPromote,
  onClose,
  onRemoved,
}: Readonly<RemoveRepoConfirmModalProps>) {
  const [error, setError] = useState<string | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  const mutation = useMutation({
    mutationFn: () => api.git.removeRepository(boardKey, repo.slug),
    onSuccess: () => {
      onRemoved();
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 403) {
        setError("Bu işlem için admin yetkisi gerekli.");
      } else {
        setError(err instanceof Error ? err.message : "Depo kaldırılamadı.");
      }
    },
  });

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    confirmRef.current?.focus();
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 backdrop-blur-sm"
      style={{ background: "var(--bg-overlay)" }}
    >
      {/* Dismiss surface — native button keeps click-outside keyboard-operable
          without a handler on a non-interactive element (S6847/S6848). */}
      <button
        type="button"
        aria-label="Kapat"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />
      <dialog
        className="card relative z-10 w-full max-w-md space-y-4 p-6"
        style={{
          background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--hairline-cyan)",
          boxShadow: "var(--shadow-glass)",
        }}
        aria-modal="true"
        aria-labelledby="remove-repo-title"
        data-testid="remove-repo-modal"
      >
        <div className="flex items-center justify-between">
          <h2 id="remove-repo-title" className="text-lg font-semibold text-danger">
            Depoyu Kaldır
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 hover:bg-raised"
            aria-label="Kapat"
          >
            <X className="h-5 w-5 text-text-muted" />
          </button>
        </div>

        <div
          className="flex items-start gap-3 rounded-md bg-danger-soft px-4 py-3 text-sm text-danger"
          role="note"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-1">
            <p>
              <span className="font-medium">{repo.name}</span>{" "}
              (<code className="mono">{repo.slug}</code>) bu board'dan kaldırılacak.
            </p>
            {willAutoPromote && (
              <p className="text-xs" data-testid="auto-promote-warning">
                Birincil depoyu kaldırıyorsunuz — en eski kalan depo otomatik
                olarak birincil olacak.
              </p>
            )}
          </div>
        </div>

        {error && (
          <div
            className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
            role="alert"
            data-testid="remove-repo-error"
          >
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            className="btn-ghost text-sm"
            onClick={onClose}
            disabled={mutation.isPending}
          >
            İptal
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="btn px-4 py-2 text-sm font-medium text-text-on-accent disabled:opacity-50"
            style={{ backgroundColor: "var(--danger)" }}
            onClick={() => {
              setError(null);
              mutation.mutate();
            }}
            disabled={mutation.isPending}
            aria-busy={mutation.isPending}
            data-testid="remove-repo-confirm-btn"
          >
            {mutation.isPending ? "Kaldırılıyor..." : "Kaldır"}
          </button>
        </div>
      </dialog>
    </div>
  );
}
