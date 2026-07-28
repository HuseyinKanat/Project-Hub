import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiRequestError } from "@/api/client";
import type { BoardResponse } from "@/types/api";
import {
  BOARD_DESCRIPTION_MAX,
  BOARD_KEY_MAX,
  BOARD_NAME_MAX,
  BOARD_PROJECT_TYPE_MAX,
  DEFAULT_PROJECT_TYPE,
  isBoardSubmittable,
  mapApiErrorToForm,
  normalizeBoardKey,
  toBoardCreatePayload,
  validateBoardForm,
  type BoardFormErrors,
} from "@/components/newBoard";

interface NewBoardDialogProps {
  open: boolean;
  onClose: () => void;
}

// Free-typing MUST remain allowed (project_type is a free-form str server-side,
// NOT an enum) — these are <datalist> SUGGESTIONS only (Architect Decision 2).
const PROJECT_TYPE_SUGGESTIONS = ["web_app", "unity", "android", "ios", "ml"];

export function NewBoardDialog({ open, onClose }: Readonly<NewBoardDialogProps>) {
  const qc = useQueryClient();
  const keyRef = useRef<HTMLInputElement>(null);

  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [projectType, setProjectType] = useState(DEFAULT_PROJECT_TYPE);
  const [errors, setErrors] = useState<BoardFormErrors>({});
  const [created, setCreated] = useState<BoardResponse | null>(null);

  useEffect(() => {
    if (open) {
      setKey("");
      setName("");
      setDescription("");
      setProjectType(DEFAULT_PROJECT_TYPE);
      setErrors({});
      setCreated(null);
      setTimeout(() => keyRef.current?.focus(), 0);
    }
  }, [open]);

  const fields = { key, name, description, project_type: projectType };
  const submittable = useMemo(() => isBoardSubmittable(fields), [fields]);

  const createMut = useMutation({
    mutationFn: () => api.createBoard(toBoardCreatePayload(fields)),
    onSuccess: (board) => {
      // AC3 — list reflects the new board without a manual refresh.
      qc.invalidateQueries({ queryKey: ["boards"] });
      // Show a success state carrying the member path (AC7) rather than navigating
      // away immediately, so the add-members link stays visible.
      setCreated(board);
    },
    onError: (err) => {
      if (err instanceof ApiRequestError) {
        setErrors(mapApiErrorToForm(err.status, err.body, err.message));
      } else {
        setErrors({ form: (err as Error).message });
      }
    },
  });

  if (!open) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const clientErrors = validateBoardForm(fields);
    if (Object.keys(clientErrors).length > 0) {
      setErrors(clientErrors);
      return;
    }
    setErrors({});
    try {
      await createMut.mutateAsync();
    } catch {
      // onError already mapped it onto `errors`; swallow so the promise rejection
      // doesn't bubble to an unhandled rejection.
    }
  }

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

      {created ? (
        <section
          role="dialog"
          aria-modal="true"
          aria-label="Board oluşturuldu"
          className="relative z-10 w-full max-w-md space-y-3 rounded-lg border p-4"
          style={{
            background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            borderColor: "var(--hairline-cyan)",
            boxShadow: "var(--shadow-glass)",
          }}
        >
          <h2 className="text-lg font-semibold text-text-primary">
            Board oluşturuldu 🎉
          </h2>
          <p className="text-sm text-text-secondary">
            <span className="mono rounded-sm border border-hairline-cyan bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent">
              {created.key}
            </span>{" "}
            {created.name} oluşturuldu ve admin olarak eklendin.
          </p>
          <p className="text-sm text-text-secondary">
            Ekibini eklemek için board ayarlarındaki üyeler bölümünü kullan.
          </p>
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Link
              to={`/boards/${created.key}/settings`}
              className="btn-ghost text-sm"
              onClick={onClose}
            >
              Üye ekle
            </Link>
            <Link
              to={`/boards/${created.key}`}
              className="btn-primary text-sm"
              onClick={onClose}
            >
              Board'a git
            </Link>
          </div>
        </section>
      ) : (
        <form
          onSubmit={submit}
          role="dialog"
          aria-modal="true"
          aria-label="Yeni board"
          className="relative z-10 w-full max-w-md space-y-3 rounded-lg border p-4"
          style={{
            background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            borderColor: "var(--hairline-cyan)",
            boxShadow: "var(--shadow-glass)",
          }}
        >
          <h2 className="text-lg font-semibold text-text-primary">Yeni board</h2>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-secondary">
              Key <span className="text-text-muted">(1–{BOARD_KEY_MAX} karakter)</span>
            </span>
            <input
              ref={keyRef}
              className="input mono uppercase"
              value={key}
              onChange={(e) => setKey(normalizeBoardKey(e.target.value))}
              required
              maxLength={BOARD_KEY_MAX}
              aria-invalid={!!errors.key}
              aria-describedby={errors.key ? "nb-key-err" : undefined}
            />
            {errors.key && (
              <span id="nb-key-err" className="block text-xs text-danger" role="alert">
                {errors.key}
              </span>
            )}
          </label>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-secondary">İsim</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={BOARD_NAME_MAX}
              aria-invalid={!!errors.name}
              aria-describedby={errors.name ? "nb-name-err" : undefined}
            />
            {errors.name && (
              <span id="nb-name-err" className="block text-xs text-danger" role="alert">
                {errors.name}
              </span>
            )}
          </label>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-secondary">
              Proje tipi
            </span>
            <input
              className="input"
              list="nb-project-types"
              value={projectType}
              onChange={(e) => setProjectType(e.target.value)}
              maxLength={BOARD_PROJECT_TYPE_MAX}
              aria-invalid={!!errors.project_type}
              aria-describedby={errors.project_type ? "nb-ptype-err" : undefined}
            />
            <datalist id="nb-project-types">
              {PROJECT_TYPE_SUGGESTIONS.map((t) => (
                <option key={t} value={t} />
              ))}
            </datalist>
            {errors.project_type && (
              <span id="nb-ptype-err" className="block text-xs text-danger" role="alert">
                {errors.project_type}
              </span>
            )}
          </label>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-text-secondary">
              Açıklama <span className="text-text-muted">(opsiyonel)</span>
            </span>
            <textarea
              className="input text-sm"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={BOARD_DESCRIPTION_MAX}
              aria-invalid={!!errors.description}
              aria-describedby={errors.description ? "nb-desc-err" : undefined}
            />
            {errors.description && (
              <span id="nb-desc-err" className="block text-xs text-danger" role="alert">
                {errors.description}
              </span>
            )}
          </label>

          {errors.form && (
            <p className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger" role="alert">
              {errors.form}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn-ghost text-sm"
              onClick={onClose}
              disabled={createMut.isPending}
            >
              Vazgeç
            </button>
            <button
              type="submit"
              className="btn-primary text-sm"
              disabled={createMut.isPending || !submittable}
            >
              {createMut.isPending ? "Oluşturuluyor…" : "Oluştur"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
