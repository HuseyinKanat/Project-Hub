/**
 * DetachConfirmModal — G13 (PH-162)
 *
 * Double-confirm modal for detaching (deleting) the board's repository config.
 * Requires user to type the board key (case-sensitive) to confirm (AC-F7).
 *
 * Props:
 *  - boardKey: string
 *  - onClose: () => void      — called on cancel / after detach success
 *
 * A11y:
 *  - role="dialog", aria-modal="true"
 *  - Confirmation input has aria-label + aria-describedby error
 *  - ESC closes
 */

import { useState, useEffect, useCallback } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X, AlertTriangle } from "lucide-react";
import { api } from "@/api/client";
import { onActivateKeyDown, stopActivationKeyDown } from "@/lib/a11y";

interface DetachConfirmModalProps {
  boardKey: string;
  onClose: () => void;
}

export function DetachConfirmModal({ boardKey, onClose }: Readonly<DetachConfirmModalProps>) {
  const qc = useQueryClient();
  const [inputKey, setInputKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.detachRepository(boardKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["git", boardKey, "status"] });
      onClose();
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Detach başarısız.");
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
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  const handleConfirm = () => {
    if (inputKey !== boardKey) {
      setError(`Board key'i büyük-küçük harf duyarlı olarak yazın: "${boardKey}"`);
      return;
    }
    setError(null);
    mutation.mutate();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 backdrop-blur-sm"
      style={{ background: "var(--bg-overlay)" }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="detach-modal-title"
      onClick={onClose}
      onKeyDown={onActivateKeyDown(onClose)}
    >
      <div
        className="card w-full max-w-md space-y-4 p-6"
        style={{
          background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--hairline-cyan)",
          boxShadow: "var(--shadow-glass)",
        }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={stopActivationKeyDown}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2
            id="detach-modal-title"
            className="text-lg font-semibold text-danger"
          >
            Repository'yi Ayır
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

        {/* Warning */}
        <div
          className="flex items-start gap-3 rounded-md bg-danger-soft px-4 py-3 text-sm text-danger"
          role="note"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-1">
            <p className="font-medium">Bu işlem geri alınamaz.</p>
            <ul className="list-inside list-disc text-xs">
              <li>BranchGraph sekmesi boşalır.</li>
              <li>Yeniden bağlandığında commit'ler tekrar backfill edilir.</li>
              <li>Mevcut hook kurulumları geçersiz olur.</li>
            </ul>
          </div>
        </div>

        {/* Confirmation input */}
        <div className="space-y-1">
          <label
            htmlFor="detach-confirm-key"
            className="block text-sm font-medium text-text-secondary"
          >
            Onaylamak için board key'i yazın:{" "}
            <code className="mono rounded bg-inset px-1 text-text-secondary">{boardKey}</code>
          </label>
          <input
            id="detach-confirm-key"
            type="text"
            value={inputKey}
            onChange={(e) => {
              setInputKey(e.target.value);
              setError(null);
            }}
            placeholder={boardKey}
            className={`input mono font-mono ${error ? "border-danger" : ""}`}
            aria-describedby={error ? "detach-key-error" : undefined}
            disabled={mutation.isPending}
            autoFocus
            data-testid="detach-confirm-input"
          />
          {error && (
            <p
              id="detach-key-error"
              className="text-xs text-danger"
              role="alert"
            >
              {error}
            </p>
          )}
        </div>

        {/* Actions */}
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
            type="button"
            className="btn px-4 py-2 text-sm font-medium text-text-on-accent disabled:opacity-50"
            style={{ backgroundColor: "var(--danger)" }}
            onClick={handleConfirm}
            disabled={mutation.isPending || !inputKey}
            aria-busy={mutation.isPending}
            data-testid="detach-confirm-btn"
          >
            {mutation.isPending ? "Ayrılıyor..." : "Ayır"}
          </button>
        </div>
      </div>
    </div>
  );
}
