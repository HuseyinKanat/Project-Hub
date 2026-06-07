/**
 * RepositoryOperationsPanel — G13 (PH-162)
 *
 * Admin-only operations panel for a connected repository:
 * 1. "Şimdi Yenile" — POST /git/refresh with Bearer auth (AC-F5)
 * 2. Hook snippet — copyable bash command with placeholders (AC-F9)
 * 3. "Rotate refresh secret" — opens RotateSecretModal (AC-F6)
 * 4. "Detach" — opens DetachConfirmModal (AC-F7)
 *
 * When status.connected=false, buttons are NOT rendered (AC-F11).
 * When !isAdmin, component is not rendered by parent (AC-F8).
 *
 * A11y:
 *  - All buttons have descriptive aria-labels
 *  - Toasts for refresh feedback
 *  - Code block has role="region" + aria-label
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, KeyRound, Unlink, Copy, Check } from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import { RotateSecretModal } from "./RotateSecretModal";
import { DetachConfirmModal } from "./DetachConfirmModal";

// Simple inline toast message (reuse Toast pattern from existing components)
function InlineToast({
  message,
  type = "success",
}: Readonly<{
  message: string;
  type?: "success" | "error" | "info";
}>) {
  const colorMap = {
    success: "bg-success-soft text-success",
    error: "bg-danger-soft text-danger",
    info: "bg-info-soft text-info",
  };
  return (
    <div
      className={`rounded-md px-3 py-2 text-sm ${colorMap[type]}`}
      role="status"
      aria-live="polite"
    >
      {message}
    </div>
  );
}

function CopyCodeBlock({ code, label }: Readonly<{ code: string; label: string }>) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      role="region"
      aria-label={label}
      className="relative rounded-md bg-inset border border-hairline p-4"
    >
      <pre className="mono overflow-x-auto text-xs text-text-primary">
        <code>{code}</code>
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        className="absolute right-2 top-2 inline-flex items-center gap-1 rounded bg-raised px-2 py-1 text-xs text-text-secondary hover:bg-overlay"
        aria-label={copied ? "Kopyalandı" : "Kopyala"}
      >
        {copied ? (
          <Check className="h-3 w-3 text-success" />
        ) : (
          <Copy className="h-3 w-3" />
        )}
        {copied ? "Kopyalandı" : "Kopyala"}
      </button>
    </div>
  );
}

interface RepositoryOperationsPanelProps {
  boardKey: string;
  connected: boolean;
  /** local_path from status; used to build hook snippet */
  localPath?: string | null;
  /** Backend base URL for hook snippet */
  backendUrl?: string;
}

export function RepositoryOperationsPanel({
  boardKey,
  connected,
  localPath,
  backendUrl = "http://localhost:8000",
}: Readonly<RepositoryOperationsPanelProps>) {
  const qc = useQueryClient();
  const [refreshMessage, setRefreshMessage] = useState<{
    text: string;
    type: "success" | "error" | "info";
  } | null>(null);
  const [showRotateModal, setShowRotateModal] = useState(false);
  const [showDetachModal, setShowDetachModal] = useState(false);

  const refreshMutation = useMutation({
    mutationFn: () => api.git.refresh(boardKey, { useBearer: true }),
    onSuccess: (data) => {
      const statusMsg =
        data.status === "coalesced"
          ? "Senkron tamam (kuyruğa alındı — debounce)"
          : "Yenileme kuyrukta";
      setRefreshMessage({ text: statusMsg, type: "success" });
      // Invalidate status so last_synced_at updates after WS event arrives
      qc.invalidateQueries({ queryKey: ["git", boardKey, "status"] });
      setTimeout(() => setRefreshMessage(null), 5000);
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 403) {
        setRefreshMessage({ text: "Yetki yok", type: "error" });
      } else if (err instanceof ApiRequestError && err.status === 503) {
        setRefreshMessage({
          text: "Refresh disabled in settings — kontrol için admin ile iletişime geç",
          type: "error",
        });
      } else {
        setRefreshMessage({
          text: err instanceof Error ? err.message : "Yenileme başarısız.",
          type: "error",
        });
      }
      setTimeout(() => setRefreshMessage(null), 6000);
    },
  });

  // Hook snippet — always show when connected (AC-F9)
  const hookSnippet = `bash scripts/install-git-hook.sh \\
  ${localPath ?? "<repo-path>"} \\
  ${boardKey} \\
  ${backendUrl} \\
  <secret>`;

  if (!connected) {
    // AC-F11: when not connected, only show hint to connect first
    return null;
  }

  return (
    <div className="space-y-6">
      {/* Operations */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-text-secondary">
          İşlemler
        </h3>

        <div className="flex flex-wrap gap-2">
          {/* Refresh */}
          <button
            type="button"
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            className="btn-primary inline-flex items-center gap-2 text-sm"
            aria-label="Şimdi yenile — Git sync tetikle"
            data-testid="refresh-btn"
          >
            <RefreshCw
              className={`h-4 w-4 ${refreshMutation.isPending ? "animate-spin" : ""}`}
            />
            {refreshMutation.isPending ? "Yenileniyor..." : "Şimdi Yenile"}
          </button>

          {/* Rotate secret */}
          <button
            type="button"
            onClick={() => setShowRotateModal(true)}
            className="btn btn-secondary inline-flex items-center gap-2 py-2 text-sm"
            aria-label="Refresh secret döndür"
            data-testid="rotate-secret-btn"
          >
            <KeyRound className="h-4 w-4" />
            Rotate refresh secret
          </button>

          {/* Detach */}
          <button
            type="button"
            onClick={() => setShowDetachModal(true)}
            className="inline-flex items-center gap-2 rounded-md border border-danger px-3 py-2 text-sm font-medium text-danger hover:bg-danger-soft"
            aria-label="Repository'yi ayır"
            data-testid="detach-btn"
          >
            <Unlink className="h-4 w-4" />
            Detach
          </button>
        </div>

        {refreshMessage && (
          <InlineToast message={refreshMessage.text} type={refreshMessage.type} />
        )}
      </div>

      {/* Hook snippet (AC-F9) */}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-text-secondary">
          Git Hook Kurulum
        </h3>
        <p className="text-xs text-text-muted">
          Deponuzdaki sunucuda aşağıdaki komutu çalıştırın.{" "}
          <strong>&lt;secret&gt;</strong> için yukarıdaki "Rotate refresh secret"
          butonuna tıklayın.
        </p>
        <CopyCodeBlock code={hookSnippet} label="Hook kurulum komutu" />
        <p className="text-xs text-text-muted">
          Secret almak için yukarıdaki "Rotate refresh secret" butonuna tıklayın.
        </p>
      </div>

      {/* Modals */}
      {showRotateModal && (
        <RotateSecretModal
          boardKey={boardKey}
          onClose={() => setShowRotateModal(false)}
        />
      )}
      {showDetachModal && (
        <DetachConfirmModal
          boardKey={boardKey}
          onClose={() => setShowDetachModal(false)}
        />
      )}
    </div>
  );
}
