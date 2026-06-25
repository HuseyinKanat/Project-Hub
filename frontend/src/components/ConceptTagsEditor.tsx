import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiRequestError, api } from "@/api/client";
import { ConceptTagChip } from "@/components/ConceptTagChip";
import type { ConceptTag, TicketResponse } from "@/types/api";

interface ConceptTagsEditorProps {
  ticketKey: string;
  boardKey: string;
  /** The ticket's CURRENT concept_tags (embedded summary shape). */
  tags: ConceptTag[];
  /** Gates the picker + remove × (the attach/detach surface). */
  canEdit: boolean;
}

/** Human-readable inline error for a mutation/query failure (403-aware). */
function errorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (err.status === 403) {
      return "Bu işlem için yetkiniz yok (tag.assign / tag.read).";
    }
    return err.message;
  }
  return (err as Error)?.message ?? "Bilinmeyen hata";
}

/**
 * ConceptTagsEditor — PH-276 (epic PH-271).
 *
 * Mirrors the FieldEditor outer chrome (`card` → `field-head` + `field-body`) so
 * it sits visually with the other field cards on TicketDetail, but the body is a
 * structured tag picker (NOT free text). Current tags render as removable
 * ConceptTagChips; a native `<select>` of the AVAILABLE tags (all tags minus the
 * already-attached) drives attach.
 *
 * Cache strategy (§D — invalidate-on-success, NOT optimistic): attach/detach each
 * return the AUTHORITATIVE full TicketResponse, so on success we
 * `setQueryData(["ticket", key], updated)` (zero-latency exact write) and
 * invalidate `["tickets", boardKey]` so the board card row refreshes. A chip is
 * shown ONLY after the server confirms — so an un-propagated-caps 403 surfaces a
 * graceful inline message with NO phantom chip (the reason optimistic was rejected).
 */
export function ConceptTagsEditor({
  ticketKey,
  boardKey,
  tags,
  canEdit,
}: Readonly<ConceptTagsEditorProps>) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState("");

  // Available-tag picker list (global taxonomy). Only fetched when editing is
  // permitted — a non-editor never needs it and shouldn't eat a 403.
  const allTagsQuery = useQuery({
    queryKey: ["concept-tags"],
    queryFn: () => api.listConceptTags(),
    enabled: canEdit,
    retry: false,
  });

  const onMutationSuccess = (updated: TicketResponse) => {
    qc.setQueryData(["ticket", ticketKey], updated);
    qc.invalidateQueries({ queryKey: ["tickets", boardKey] });
    setSelected("");
  };

  const attachMutation = useMutation({
    mutationFn: (tagId: string) => api.attachConceptTag(ticketKey, tagId),
    onSuccess: onMutationSuccess,
  });

  const detachMutation = useMutation({
    mutationFn: (tagId: string) => api.detachConceptTag(ticketKey, tagId),
    onSuccess: onMutationSuccess,
  });

  const attachedIds = new Set(tags.map((t) => t.id));
  const available = (allTagsQuery.data?.tags ?? []).filter(
    (t) => !attachedIds.has(t.id),
  );

  const mutationError =
    (attachMutation.error && errorMessage(attachMutation.error)) ||
    (detachMutation.error && errorMessage(detachMutation.error)) ||
    null;
  const pickerError =
    canEdit && allTagsQuery.isError ? errorMessage(allTagsQuery.error) : null;
  const busy = attachMutation.isPending || detachMutation.isPending;

  return (
    <section className="card" style={{ padding: 0 }}>
      <div className="field-head">
        <span className="field-title">Concept Tags</span>
      </div>

      <div className="field-body">
        <div className="space-y-3">
          {tags.length === 0 ? (
            <p className="text-xs text-text-muted">Henüz concept tag yok.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {tags.map((tag) => (
                <ConceptTagChip
                  key={tag.id}
                  tag={tag}
                  onRemove={
                    canEdit ? () => detachMutation.mutate(tag.id) : undefined
                  }
                  removeDisabled={busy}
                />
              ))}
            </div>
          )}

          {canEdit && (
            <div className="flex items-center gap-2">
              <select
                className="input text-xs"
                value={selected}
                disabled={busy || allTagsQuery.isLoading}
                onChange={(e) => {
                  const tagId = e.target.value;
                  setSelected(tagId);
                  if (tagId) attachMutation.mutate(tagId);
                }}
                aria-label="Concept tag ekle"
              >
                <option value="">
                  {allTagsQuery.isLoading ? "Yükleniyor…" : "Tag ekle…"}
                </option>
                {available.map((tag) => (
                  <option key={tag.id} value={tag.id}>
                    {tag.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {(mutationError || pickerError) && (
            <p className="text-xs text-danger" role="alert">
              {mutationError ?? pickerError}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
