import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiRequestError, api } from "@/api/client";
import type { AttachmentResponse } from "@/types/api";

import { selectorToSlug, slugToSelector, type PhaseSelector } from "./grouping";
import { PhaseSelect } from "./PhaseSelect";

/**
 * AttachmentPhaseEditor — PH-314
 *
 * Inline "edit phase" affordance for one evidence row (canUpdate-gated). Seeds the
 * shared `PhaseSelect` from the attachment's CURRENT phase (`slugToSelector`) and,
 * on save, PATCHes the new slug — `selectorToSlug` yields `null` for the "— faz yok —"
 * choice, which the backend key-presence contract reads as CLEAR the phase.
 *
 * On success invalidates ["ticket-attachments", key] so the PH-312 story view
 * re-groups the item instantly (AC4). A 403/404/network error is NON-destructive
 * (AC8): the editor stays open with an inline reason and the row/list is untouched
 * — no optimistic re-group.
 */
export function AttachmentPhaseEditor({
  ticketKey,
  attachment,
  onClose,
}: Readonly<{
  ticketKey: string;
  attachment: AttachmentResponse;
  onClose: () => void;
}>) {
  const qc = useQueryClient();
  const seed = slugToSelector(attachment.phase);
  const [sel, setSel] = useState<PhaseSelector>(seed.sel);
  const [iterN, setIterN] = useState(seed.iterN);
  const [error, setError] = useState<string | null>(null);

  const updateMut = useMutation({
    mutationFn: () =>
      api.updateAttachment(ticketKey, attachment.id, {
        phase: selectorToSlug(sel, iterN),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-attachments", ticketKey] });
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 403) {
        setError("Bu kanıtı düzenleme yetkin yok.");
      } else if (err instanceof ApiRequestError && err.status === 404) {
        setError("Kanıt bulunamadı (silinmiş olabilir).");
      } else {
        setError(err instanceof Error ? err.message : "Güncelleme başarısız.");
      }
    },
  });

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-hairline bg-raised p-2.5"
      aria-label="Fazı düzenle"
    >
      <PhaseSelect
        idPrefix={`phase-edit-${attachment.id}`}
        sel={sel}
        iterN={iterN}
        onSelChange={setSel}
        onIterNChange={setIterN}
        disabled={updateMut.isPending}
      />

      {error && (
        <p role="alert" className="text-[11px] text-danger">
          {error}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => {
            setError(null);
            updateMut.mutate();
          }}
          disabled={updateMut.isPending}
          className="btn-secondary btn-sm"
        >
          {updateMut.isPending ? "Kaydediliyor…" : "Kaydet"}
        </button>
        <button
          type="button"
          onClick={onClose}
          disabled={updateMut.isPending}
          className="btn-ghost btn-sm"
        >
          İptal
        </button>
      </div>
    </div>
  );
}
