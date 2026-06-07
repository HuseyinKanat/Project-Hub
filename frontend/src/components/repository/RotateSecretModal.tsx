/**
 * RotateSecretModal — G13 (PH-162)
 *
 * One-shot modal: admin triggers POST /repository/rotate-refresh-secret.
 * Displays plaintext secret + full hook-install command (both copyable) once.
 * On close plaintext is removed from React state (AC-F6).
 *
 * Props:
 *  - boardKey: string
 *  - onClose: () => void   — called after user clicks "I've stored it"
 *
 * A11y:
 *  - role="dialog", aria-modal="true", focus-trap via autoFocus on close button
 *  - ESC closes
 */

import { useState, useEffect, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { Copy, Check, X, AlertTriangle } from "lucide-react";
import { api } from "@/api/client";
import { onActivateKeyDown, stopActivationKeyDown } from "@/lib/a11y";

interface RotateSecretModalProps {
  boardKey: string;
  onClose: () => void;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="ml-2 inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-raised"
      aria-label={copied ? "Kopyalandı" : "Kopyala"}
    >
      {copied ? (
        <Check className="h-3 w-3 text-success" />
      ) : (
        <Copy className="h-3 w-3" />
      )}
      {copied ? "Kopyalandı" : "Kopyala"}
    </button>
  );
}

export function RotateSecretModal({ boardKey, onClose }: RotateSecretModalProps) {
  const [confirmed, setConfirmed] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [hookCommand, setHookCommand] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.rotateRefreshSecret(boardKey),
    onSuccess: (data) => {
      setSecret(data.refresh_secret);
      setHookCommand(data.hook_install_command);
    },
  });

  // Close on ESC
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

  const handleClose = () => {
    // Clear plaintext from state before closing (AC-F6)
    setSecret(null);
    setHookCommand(null);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 backdrop-blur-sm"
      style={{ background: "var(--bg-overlay)" }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="rotate-secret-title"
      onClick={handleClose}
      onKeyDown={onActivateKeyDown(handleClose)}
    >
      <div
        className="card w-full max-w-lg space-y-4 p-6"
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
            id="rotate-secret-title"
            className="text-lg font-semibold text-text-primary"
          >
            Refresh Secret Döndür
          </h2>
          <button
            type="button"
            onClick={handleClose}
            className="rounded p-1 hover:bg-raised"
            aria-label="Kapat"
          >
            <X className="h-5 w-5 text-text-muted" />
          </button>
        </div>

        {/* Before confirmation */}
        {!confirmed && !secret && (
          <>
            <div
              className="flex items-start gap-3 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
              role="note"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <p>
                Bu işlem mevcut hook kurulumlarını geçersiz kılar. Eski secret ile
                gönderilen webhook'lar artık çalışmayacak.
              </p>
            </div>

            <p className="text-sm text-text-secondary">
              Devam etmek istediğinizden emin misiniz?
            </p>

            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="btn-ghost text-sm"
                onClick={handleClose}
              >
                İptal
              </button>
              <button
                type="button"
                className="btn px-4 py-2 text-sm font-medium text-text-on-accent"
                style={{ backgroundColor: "var(--warning)" }}
                onClick={() => {
                  setConfirmed(true);
                  mutation.mutate();
                }}
              >
                Döndür
              </button>
            </div>
          </>
        )}

        {/* Loading */}
        {confirmed && mutation.isPending && (
          <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-hairline border-t-accent" />
            <span>Yeni secret oluşturuluyor...</span>
          </div>
        )}

        {/* Error */}
        {mutation.isError && (
          <div
            className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
            role="alert"
          >
            Hata: {mutation.error instanceof Error ? mutation.error.message : "Bilinmeyen hata"}
          </div>
        )}

        {/* Success — show secret once */}
        {secret && (
          <>
            <div
              className="rounded-md bg-success-soft px-4 py-3 text-sm text-success"
              role="note"
            >
              Yeni secret oluşturuldu. Bunu hemen kopyalayın — bir daha
              gösterilmeyecek.
            </div>

            {/* Secret */}
            <div className="space-y-1">
              <p className="text-xs font-medium text-text-secondary">
                Refresh Secret
              </p>
              <div className="flex items-start rounded-md bg-inset border border-hairline p-3">
                <code
                  className="flex-1 break-all font-mono text-xs text-text-primary"
                  data-testid="rotate-secret-value"
                >
                  {secret}
                </code>
                <CopyButton text={secret} />
              </div>
            </div>

            {/* Hook command */}
            {hookCommand && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-text-secondary">
                  Hook kurulum komutu
                </p>
                <div className="flex items-start rounded-md bg-inset border border-hairline p-3">
                  <code
                    className="flex-1 break-all font-mono text-xs text-text-primary"
                    data-testid="rotate-hook-command"
                  >
                    {hookCommand}
                  </code>
                  <CopyButton text={hookCommand} />
                </div>
              </div>
            )}

            <button
              type="button"
              className="btn-primary w-full text-sm"
              onClick={handleClose}
              autoFocus
              data-testid="rotate-stored-btn"
            >
              Sakladım, kapat
            </button>
          </>
        )}
      </div>
    </div>
  );
}
