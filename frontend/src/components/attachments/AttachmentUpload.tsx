import { FormEvent, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { UploadCloud } from "lucide-react";

import { ApiRequestError, api } from "@/api/client";
import type { AttachmentKind, AttachmentResponse } from "@/types/api";

import {
  isSpecKind,
  selectorToSlug,
  suggestNextIter,
  type PhaseSelector,
} from "./grouping";
import { PhaseSelect, isIterSelector } from "./PhaseSelect";

const ACCEPT = "image/png,image/jpeg,video/mp4,application/json,text/plain";

const KIND_OPTIONS: { value: "" | AttachmentKind; label: string }[] = [
  { value: "", label: "Otomatik (other)" },
  { value: "screenshot", label: "screenshot" },
  { value: "recording", label: "recording" },
  { value: "video", label: "video" },
  { value: "log", label: "log" },
  { value: "report", label: "report" },
  { value: "other", label: "other" },
];

/** Map an upload failure to an inline Turkish message keyed off the HTTP status. */
function friendlyError(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (err.status === 413) return "Dosya çok büyük (limit 25MiB)";
    if (err.status === 415) return "Desteklenmeyen tür";
    if (err.status === 403) return "Yetkin yok";
    if (err.status === 401) return "Oturum doğrulanamadı — tekrar giriş yap";
    return err.message;
  }
  return "Yükleme başarısız";
}

/**
 * AttachmentUpload — PH-297 / PH-314
 *
 * Labeled file input + optional kind/run_id/PHASE, backed by the dedicated XHR
 * uploader (real upload-progress + multipart boundary intact). Surfaces a
 * role="progressbar" during upload and maps 413/415/403 to inline aria-live
 * messages. On success it invalidates ["ticket-attachments", ticketKey] so the
 * list refreshes immediately (belt to the WS `attachment_added` braces).
 *
 * PH-314: a phase picker tags the upload with a story slug (repro, iteration
 * fail/pass, before, after). Picking an iteration option prefills N with
 * `suggestNextIter(items)` (highest observed iteration + 1). The composed slug is
 * `null` when phaseless (AC3, back-compat). `items` is the current evidence list
 * (drives the N suggestion).
 */
export function AttachmentUpload({
  ticketKey,
  items,
}: Readonly<{ ticketKey: string; items: AttachmentResponse[] }>) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<"" | AttachmentKind>("");
  const [runId, setRunId] = useState("");
  const [phaseSel, setPhaseSel] = useState<PhaseSelector>("");
  const [iterN, setIterN] = useState(1);
  const [progress, setProgress] = useState(0);

  // The default iteration number when the user switches to an `iter-*` phase:
  // highest observed iteration across existing evidence + 1 (1 when none). AC1/AC9.
  const suggestedIter = useMemo(() => suggestNextIter(items), [items]);

  const uploadMut = useMutation({
    mutationFn: (f: File) =>
      api.uploadAttachment(ticketKey, f, {
        kind: kind || undefined,
        runId: runId || undefined,
        // null → phaseless (uploadAttachment only appends a non-empty phase).
        phase: selectorToSlug(phaseSel, iterN),
        onProgress: setProgress,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-attachments", ticketKey] });
      setFile(null);
      setRunId("");
      setKind("");
      setPhaseSel("");
      setProgress(0);
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file || uploadMut.isPending) return;
    setProgress(0);
    uploadMut.mutate(file);
  }

  // Switching TO an iteration phase prefills N with the suggestion (AC1); the user
  // can then adjust the number input freely.
  function onPhaseSelChange(next: PhaseSelector) {
    setPhaseSel(next);
    if (isIterSelector(next)) setIterN(suggestedIter);
  }

  const busy = uploadMut.isPending;
  // AC7 defensive guard: phase is evidence-only; a spec kind disables the picker
  // (unreachable via KIND_OPTIONS, which excludes usecase/testcase — pins invariant).
  const phaseDisabled = busy || isSpecKind(kind);

  return (
    <form
      onSubmit={onSubmit}
      className="flex flex-col gap-2.5 rounded-md border border-hairline bg-raised p-3"
      aria-label="Kanıt yükle"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor="attachment-file" className="text-[11px] text-text-muted">
            Dosya
          </label>
          <input
            id="attachment-file"
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            disabled={busy}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              uploadMut.reset();
            }}
            className="text-xs text-text-secondary file:mr-2 file:rounded file:border-0 file:bg-surface file:px-2 file:py-1 file:text-xs file:text-text-primary"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="attachment-kind" className="text-[11px] text-text-muted">
            Tür
          </label>
          <select
            id="attachment-kind"
            value={kind}
            disabled={busy}
            onChange={(e) => setKind(e.target.value as "" | AttachmentKind)}
            className="input"
            style={{ height: 30, paddingTop: 0, paddingBottom: 0 }}
          >
            {KIND_OPTIONS.map((o) => (
              <option key={o.value || "auto"} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="attachment-runid" className="text-[11px] text-text-muted">
            run_id (opsiyonel)
          </label>
          <input
            id="attachment-runid"
            type="text"
            value={runId}
            disabled={busy}
            onChange={(e) => setRunId(e.target.value)}
            placeholder="mcp-…"
            className="input"
            style={{ height: 30 }}
          />
        </div>

        <PhaseSelect
          idPrefix="attachment-upload"
          sel={phaseSel}
          iterN={iterN}
          onSelChange={onPhaseSelChange}
          onIterNChange={setIterN}
          disabled={phaseDisabled}
        />

        <button
          type="submit"
          disabled={!file || busy}
          className="btn-secondary btn-sm"
        >
          <UploadCloud className="h-3.5 w-3.5" />
          {busy ? "Yükleniyor…" : "Yükle"}
        </button>
      </div>

      {busy && (
        <div
          role="progressbar"
          aria-label="Yükleme ilerlemesi"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1.5 w-full overflow-hidden rounded-full bg-surface"
        >
          <div
            className="h-full rounded-full bg-accent transition-[width]"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      <p aria-live="polite" role="status" className="min-h-0">
        {uploadMut.isError && (
          <span className="text-[11px] text-danger">
            {friendlyError(uploadMut.error)}
          </span>
        )}
      </p>
    </form>
  );
}
