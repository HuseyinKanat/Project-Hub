import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { ApiRequestError, api } from "@/api/client";
import type { AttachmentResponse } from "@/types/api";

import { Modal } from "@/components/ui/Modal";

/**
 * AttachmentDeleteConfirm — PH-314
 *
 * Destructive "delete this evidence?" confirm, built on the shared accessible
 * `ui/Modal` primitive (PH-305) — so focus-trap, Escape/backdrop close, and
 * focus-return-to-trigger come for free (no hand-rolled dialog shell). Modal
 * auto-focuses the FIRST focusable, which here is the "İptal" button: a safe
 * default for a destructive action (Enter cancels, not deletes).
 *
 * Contract (AC5/AC8): confirm → DELETE → invalidate ["ticket-attachments", key]
 * ON SUCCESS ONLY, so the row drops from the story view without an optimistic
 * removal that could corrupt the list on error. Cancel/ESC/backdrop send NO
 * request. A 403/404/network failure keeps the dialog open with a non-destructive
 * inline message; the evidence list stays intact.
 */
export function AttachmentDeleteConfirm({
  ticketKey,
  attachment,
  onClose,
}: Readonly<{
  ticketKey: string;
  attachment: AttachmentResponse;
  onClose: () => void;
}>) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const deleteMut = useMutation({
    mutationFn: () => api.deleteAttachment(ticketKey, attachment.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-attachments", ticketKey] });
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 403) {
        setError("Bu kanıtı silme yetkin yok.");
      } else if (err instanceof ApiRequestError && err.status === 404) {
        setError("Kanıt zaten silinmiş olabilir.");
      } else {
        setError(err instanceof Error ? err.message : "Silme başarısız.");
      }
    },
  });

  return (
    <Modal
      onClose={onClose}
      labelledBy="attachment-delete-title"
      className="w-full max-w-md"
    >
      <div
        className="flex items-start gap-3 rounded-md bg-danger-soft px-4 py-3 text-sm text-danger"
        role="note"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <h2 id="attachment-delete-title" className="font-semibold">
            Kanıt silinsin mi?
          </h2>
          <p className="mono truncate text-xs" title={attachment.filename}>
            {attachment.filename}
          </p>
        </div>
      </div>

      {error && (
        <div
          className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger"
          role="alert"
        >
          {error}
        </div>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="btn-ghost btn-sm"
          disabled={deleteMut.isPending}
        >
          İptal
        </button>
        <button
          type="button"
          onClick={() => {
            setError(null);
            deleteMut.mutate();
          }}
          disabled={deleteMut.isPending}
          aria-busy={deleteMut.isPending}
          className="btn btn-sm px-3 font-medium text-text-on-accent disabled:opacity-50"
          style={{ backgroundColor: "var(--danger)" }}
        >
          {deleteMut.isPending ? "Siliniyor…" : "Sil"}
        </button>
      </div>
    </Modal>
  );
}
