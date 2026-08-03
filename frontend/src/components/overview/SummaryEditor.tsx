/**
 * SummaryEditor.tsx — PH-339: the edit-mode form for a board's project summary.
 * Seeded from the ALREADY-FETCHED summary (never a partial) and, on save, sends
 * the WHOLE object via the PH-338 full-replace PUT — the single most important
 * invariant here: a partial payload would NULL the sections the editor didn't
 * send (AC3 full-replace). Mirrors the NotesPanel form conventions (client
 * non-blank guard, inline error, UC E1 — a failed save PRESERVES the typed
 * content for retry).
 *
 * Fields: four free-text sections (purpose / status / progress / highlights —
 * plain textareas; humans type markdown `- ` bullets that the read view renders
 * via MarkdownRenderer) + a milestone row editor (title / target /
 * status<select> / due_date<date>, add / remove / reorder). `order` is DERIVED
 * from the row position on submit (0..n-1) so it is always valid + gap-free — no
 * raw order field to mis-key.
 *
 * a11y (AC5): every field is labelled (section textareas via `<label htmlFor>`;
 * repeated milestone inputs via `aria-label`); the save error is `role="alert"`.
 */
import { FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowDown, ArrowUp, Loader2, Plus, Save, Trash2, X } from "lucide-react";

import { api, ApiRequestError } from "@/api/client";
import { MILESTONE_STATUSES, milestoneStatusMeta } from "./milestoneStatus";
import type { BoardSummary, BoardSummaryUpsert, Milestone, MilestoneStatus } from "@/types/api";

/** Form-friendly milestone draft: "" stands in for a null target / due_date. */
interface DraftMilestone {
  title: string;
  target: string;
  status: MilestoneStatus;
  due_date: string;
}

type SectionKey = "purpose" | "status" | "progress" | "highlights";

const SECTIONS: ReadonlyArray<{
  key: SectionKey;
  label: string;
  placeholder: string;
}> = [
  { key: "purpose", label: "Amaç", placeholder: "Bu board'un amacı nedir? (markdown `- ` madde yazabilirsiniz)" },
  { key: "status", label: "Genel Durum", placeholder: "Şu anki genel durum…" },
  { key: "progress", label: "İlerleme", placeholder: "- Tamamlanan iş\n- Devam eden iş" },
  { key: "highlights", label: "Öne Çıkan Kapatılanlar", placeholder: "- Öne çıkan tamamlanan işler" },
];

/** Map a save error to friendly inline copy (403 role / 422 milestone / other). */
function saveErrorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (err.status === 403) return "Bu board'un özetini düzenleme yetkiniz yok.";
    if (err.status === 404) return "Bu board artık mevcut değil.";
    if (err.status === 422)
      return "Geçersiz kilometre taşı — her taşın başlığı dolu olmalı.";
    return err.message || "Bir şeyler ters gitti.";
  }
  if (err instanceof Error) return err.message || "Bir şeyler ters gitti.";
  return "Bir şeyler ters gitti.";
}

function toDraft(m: Milestone): DraftMilestone {
  return {
    title: m.title,
    target: m.target ?? "",
    status: m.status,
    due_date: m.due_date ?? "",
  };
}

export function SummaryEditor({
  boardKey,
  initial,
  onCancel,
  onSaved,
}: Readonly<{
  boardKey: string;
  /** The already-fetched summary to seed from (null → a fresh, empty form). */
  initial: BoardSummary | null;
  onCancel: () => void;
  onSaved: () => void;
}>) {
  const queryClient = useQueryClient();

  const [sections, setSections] = useState<Record<SectionKey, string>>({
    purpose: initial?.purpose ?? "",
    status: initial?.status ?? "",
    progress: initial?.progress ?? "",
    highlights: initial?.highlights ?? "",
  });
  const [milestones, setMilestones] = useState<DraftMilestone[]>(
    // Seed already order-ascending so the editor rows match the view timeline.
    [...(initial?.milestones ?? [])]
      .sort((a, b) => a.order - b.order)
      .map(toDraft),
  );
  const [saveError, setSaveError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (payload: BoardSummaryUpsert) =>
      api.upsertBoardSummary(boardKey, payload),
    onSuccess: () => {
      // Golden path: refetch the canonical summary + return to the read view.
      void queryClient.invalidateQueries({
        queryKey: ["board", boardKey, "summary"],
      });
      onSaved();
    },
    onError: (err) => {
      // UC E1: surface the failure inline AND KEEP the typed content for retry —
      // deliberately do NOT reset `sections` / `milestones` here.
      setSaveError(saveErrorMessage(err));
    },
  });

  const setSection = (key: SectionKey, value: string) =>
    setSections((s) => ({ ...s, [key]: value }));

  const updateMilestone = (idx: number, patch: Partial<DraftMilestone>) =>
    setMilestones((ms) => ms.map((m, i) => (i === idx ? { ...m, ...patch } : m)));

  const addMilestone = () =>
    setMilestones((ms) => [
      ...ms,
      { title: "", target: "", status: "planned", due_date: "" },
    ]);

  const removeMilestone = (idx: number) =>
    setMilestones((ms) => ms.filter((_, i) => i !== idx));

  const moveMilestone = (idx: number, delta: -1 | 1) =>
    setMilestones((ms) => {
      const next = idx + delta;
      if (next < 0 || next >= ms.length) return ms;
      const copy = [...ms];
      const a = copy[idx];
      const b = copy[next];
      // Narrow away the `T | undefined` of noUncheckedIndexedAccess (the bounds
      // are already guarded above; this is a type-level guard, never hit).
      if (!a || !b) return ms;
      copy[idx] = b;
      copy[next] = a;
      return copy;
    });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    // Client guard mirrors the backend non-blank milestone-title rule so the
    // common case never round-trips a 422 (the backend validator is still the
    // source of truth).
    if (milestones.some((m) => m.title.trim().length === 0)) {
      setSaveError("Her kilometre taşının bir başlığı olmalı.");
      return;
    }
    setSaveError(null);
    // FULL-REPLACE: always send the WHOLE object (4 sections + full milestones);
    // an empty section normalises to null; `order` is derived from row position.
    const payload: BoardSummaryUpsert = {
      purpose: sections.purpose.trim() || null,
      status: sections.status.trim() || null,
      progress: sections.progress.trim() || null,
      highlights: sections.highlights.trim() || null,
      milestones: milestones.map((m, i) => ({
        title: m.title.trim(),
        target: m.target.trim() || null,
        status: m.status,
        order: i,
        due_date: m.due_date || null,
      })),
    };
    mutation.mutate(payload);
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-5"
      aria-label="Board özetini düzenle"
      data-testid="summary-editor"
    >
      {/* Free-text sections. */}
      <div className="space-y-4">
        {SECTIONS.map(({ key, label, placeholder }) => (
          <div key={key} className="space-y-1.5">
            <label
              htmlFor={`summary-${key}`}
              className="block text-sm font-medium text-text-primary"
            >
              {label}
            </label>
            <textarea
              id={`summary-${key}`}
              className="input min-h-[4.5rem] resize-y"
              placeholder={placeholder}
              value={sections[key]}
              onChange={(e) => setSection(key, e.target.value)}
              data-testid={`summary-section-${key}`}
            />
          </div>
        ))}
      </div>

      {/* Milestone editor. */}
      <fieldset className="space-y-3 border-t border-hairline pt-4">
        <legend className="text-sm font-medium text-text-primary">
          Kilometre Taşları
        </legend>

        {milestones.length === 0 && (
          <p className="text-xs text-text-muted">
            Henüz kilometre taşı yok — aşağıdan ekleyin.
          </p>
        )}

        <ul className="space-y-3">
          {milestones.map((m, idx) => (
            <li
              key={idx}
              className="card space-y-2 p-3"
              data-testid="milestone-edit-row"
            >
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  className="input min-w-0 flex-1"
                  placeholder="Başlık *"
                  aria-label={`Kilometre taşı ${idx + 1} başlığı`}
                  value={m.title}
                  onChange={(e) => updateMilestone(idx, { title: e.target.value })}
                  data-testid="milestone-title-input"
                />
                <select
                  className="input w-auto"
                  aria-label={`Kilometre taşı ${idx + 1} durumu`}
                  value={m.status}
                  onChange={(e) =>
                    updateMilestone(idx, {
                      status: e.target.value as MilestoneStatus,
                    })
                  }
                  data-testid="milestone-status-select"
                >
                  {MILESTONE_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {milestoneStatusMeta(s).trLabel}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  className="input min-w-0 flex-1"
                  placeholder="Hedef (opsiyonel)"
                  aria-label={`Kilometre taşı ${idx + 1} hedefi`}
                  value={m.target}
                  onChange={(e) => updateMilestone(idx, { target: e.target.value })}
                  data-testid="milestone-target-input"
                />
                <input
                  type="date"
                  className="input w-auto"
                  aria-label={`Kilometre taşı ${idx + 1} bitiş tarihi`}
                  value={m.due_date}
                  onChange={(e) =>
                    updateMilestone(idx, { due_date: e.target.value })
                  }
                  data-testid="milestone-due-input"
                />
                <div className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    className="btn-ghost p-1.5 disabled:opacity-40"
                    aria-label={`Kilometre taşı ${idx + 1} yukarı taşı`}
                    disabled={idx === 0}
                    onClick={() => moveMilestone(idx, -1)}
                  >
                    <ArrowUp className="h-4 w-4" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="btn-ghost p-1.5 disabled:opacity-40"
                    aria-label={`Kilometre taşı ${idx + 1} aşağı taşı`}
                    disabled={idx === milestones.length - 1}
                    onClick={() => moveMilestone(idx, 1)}
                  >
                    <ArrowDown className="h-4 w-4" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="btn-ghost p-1.5 text-text-muted hover:text-danger"
                    aria-label={`Kilometre taşı ${idx + 1} sil`}
                    onClick={() => removeMilestone(idx)}
                    data-testid="milestone-remove"
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <button
          type="button"
          className="btn-ghost inline-flex items-center gap-1.5 text-sm"
          onClick={addMilestone}
          data-testid="milestone-add"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Kilometre taşı ekle
        </button>
      </fieldset>

      {/* Inline save error (UC E1 — all typed content above is preserved). */}
      {saveError && (
        <div
          className="flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
          role="alert"
          data-testid="summary-editor-error"
        >
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{saveError}</span>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 border-t border-hairline pt-4">
        <button
          type="button"
          className="btn-ghost inline-flex items-center gap-1.5 text-sm"
          onClick={onCancel}
          disabled={mutation.isPending}
          data-testid="summary-editor-cancel"
        >
          <X className="h-4 w-4" aria-hidden="true" />
          İptal
        </button>
        <button
          type="submit"
          className="btn-primary inline-flex items-center gap-2 text-sm"
          disabled={mutation.isPending}
          data-testid="summary-editor-save"
        >
          {mutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Save className="h-4 w-4" aria-hidden="true" />
          )}
          Kaydet
        </button>
      </div>
    </form>
  );
}
