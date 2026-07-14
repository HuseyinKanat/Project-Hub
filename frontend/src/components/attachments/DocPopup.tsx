import { useEffect, useId, useMemo, useState } from "react";
import { X } from "lucide-react";

import { api, ApiRequestError } from "@/api/client";
import { Modal } from "@/components/ui/Modal";
import type { AttachmentResponse } from "@/types/api";

import { MarkdownRenderer } from "../MarkdownRenderer";
import {
  foldJsonTopLevel,
  isJsonAttachment,
  isMarkdown,
  prettyPrintJson,
  TEXT_PREVIEW_CAP_BYTES,
} from "./grouping";

/**
 * DocPopup — PH-310 (modal shell shared via ui/Modal since PH-305)
 *
 * Accessible modal document viewer for TEXT-family evidence + spec docs (`.md`
 * AC/test-case attachments, JSON reports, `.log`/`.txt`). The a11y-critical shell
 * — overlay + native dismiss surface + focus-trap/Esc/backdrop/focus-return +
 * role="dialog" card — now comes from the shared `ui/Modal` primitive (which also
 * fixes the unstable-onClose focus-return bug, PH-305 AC2). This component keeps
 * its OWN header (filename title + close), the toolbar, and the fetched/typed body.
 *
 * Content is fetched here (self-contained loading/error/idle state, reusing
 * `api.fetchAttachmentText`) and routed by type — markdown → prose, JSON →
 * pretty + per-key fold, else → monospace `<pre>` with a line-wrap toggle. The
 * 512-KiB cap is enforced by ALL callers (both the AttachmentItem row and the
 * TicketDetail SpecDocChips gate on `isOverCap(size_bytes)` FIRST and offer
 * download instead of opening this popup). `maxBytes` passed below is only a
 * DISPLAY slice of the already-fetched body — `fetchAttachmentText` downloads the
 * WHOLE blob, so it is NOT a download guard; the caller's `size_bytes` gate is.
 *
 * External props {ticketKey, attachment, onClose} are UNCHANGED — callers
 * (AttachmentItem, TicketDetail SpecDocChips) need no edit.
 */
export function DocPopup({
  ticketKey,
  attachment,
  onClose,
}: Readonly<{
  ticketKey: string;
  attachment: AttachmentResponse;
  onClose: () => void;
}>) {
  const titleId = useId();

  const markdown = isMarkdown(attachment.content_type, attachment.filename);
  const isJson =
    !markdown && isJsonAttachment(attachment.content_type, attachment.filename);

  const [status, setStatus] = useState<"loading" | "error" | "idle">("loading");
  const [text, setText] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [wrap, setWrap] = useState(true);
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(new Set());

  // Lazy fetch on mount; `cancelled` guards a close-before-resolve unmount.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setStatus("loading");
      setErrorMsg(null);
      try {
        const t = await api.fetchAttachmentText(ticketKey, attachment.id, {
          maxBytes: TEXT_PREVIEW_CAP_BYTES,
        });
        if (cancelled) return;
        setText(t);
        setStatus("idle");
      } catch (err) {
        if (cancelled) return;
        setErrorMsg(
          err instanceof ApiRequestError
            ? err.status === 403
              ? "Bu belgeyi görüntüleme yetkiniz yok (403)."
              : `Belge yüklenemedi: ${err.message}`
            : "Belge yüklenemedi.",
        );
        setStatus("error");
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [ticketKey, attachment.id]);

  // Parse once per fetched body: a plain OBJECT root folds by top-level key; an
  // array/scalar root (or a JSON-hinted body that fails to parse) falls back to a
  // flat pretty pane / raw text. Mirrors AttachmentItem's PH-300 logic (moved here).
  const jsonView = useMemo(() => {
    if (text === null || !isJson) return null;
    const entries = foldJsonTopLevel(text);
    if (entries) return { kind: "fold" as const, entries };
    try {
      return { kind: "flat" as const, pretty: prettyPrintJson(text) };
    } catch {
      return null;
    }
  }, [text, isJson]);

  const foldableKeys =
    jsonView?.kind === "fold"
      ? jsonView.entries.filter((e) => e.body !== null).map((e) => e.key)
      : [];
  const allExpanded =
    foldableKeys.length > 0 && foldableKeys.every((k) => expandedKeys.has(k));

  function toggleKey(k: string) {
    setExpandedKeys((prev) => {
      const nextSet = new Set(prev);
      if (nextSet.has(k)) nextSet.delete(k);
      else nextSet.add(k);
      return nextSet;
    });
  }

  function toggleAll() {
    setExpandedKeys(allExpanded ? new Set() : new Set(foldableKeys));
  }

  const preClass = `mono overflow-auto rounded border border-hairline bg-raised p-2 text-[11px] leading-relaxed text-text-primary ${
    wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"
  }`;

  // Toolbar affordances: wrap toggle for any <pre> body (text / flat-json / fold
  // bodies), expand-all only when the JSON folds by top-level key. Markdown prose
  // needs neither.
  const showWrapToggle = status === "idle" && text !== null && !markdown;
  const showExpandAll =
    status === "idle" && jsonView?.kind === "fold" && foldableKeys.length > 0;

  let body: React.ReactNode;
  if (status === "loading") {
    body = <p className="text-xs text-text-muted">Yükleniyor…</p>;
  } else if (status === "error") {
    body = (
      <p
        role="alert"
        className="rounded-md bg-danger-soft px-2 py-1.5 text-xs text-danger"
      >
        {errorMsg}
      </p>
    );
  } else if (markdown && text !== null) {
    body = <MarkdownRenderer content={text} />;
  } else if (jsonView?.kind === "fold") {
    body = (
      <ul className="flex flex-col gap-1 rounded border border-hairline bg-raised p-2">
        {jsonView.entries.map((entry) => {
          const expanded = entry.body === null || expandedKeys.has(entry.key);
          return (
            <li key={entry.key} className="min-w-0">
              {entry.body !== null ? (
                <button
                  type="button"
                  onClick={() => toggleKey(entry.key)}
                  aria-expanded={expanded}
                  className="mono flex w-full items-center gap-1.5 text-left text-[11px] text-text-primary hover:opacity-80"
                >
                  <span aria-hidden="true" className="text-text-muted">
                    {expanded ? "▾" : "▸"}
                  </span>
                  <span className="shrink-0">{entry.key}</span>
                  {!expanded && (
                    <span className="truncate text-text-muted">{entry.summary}</span>
                  )}
                </button>
              ) : (
                <p className="mono flex items-center gap-1.5 text-[11px]">
                  <span aria-hidden="true" className="text-text-muted">
                    ·
                  </span>
                  <span className="shrink-0 text-text-primary">{entry.key}</span>
                  <span className="truncate text-text-muted">{entry.summary}</span>
                </p>
              )}
              {entry.body !== null && expanded && (
                <pre
                  className={`${preClass} mt-1`}
                  tabIndex={0}
                  role="region"
                  aria-label={`${entry.key} değeri`}
                  style={{ maxHeight: 320 }}
                >
                  {entry.body}
                </pre>
              )}
            </li>
          );
        })}
      </ul>
    );
  } else if (text !== null) {
    body = (
      <pre
        className={preClass}
        tabIndex={0}
        role="region"
        aria-label={`${attachment.filename} içeriği`}
      >
        {jsonView?.kind === "flat" ? jsonView.pretty : text}
      </pre>
    );
  }

  return (
    <Modal onClose={onClose} labelledBy={titleId} className="w-full max-w-3xl">
      <div className="flex items-center justify-between gap-3">
        <span
          id={titleId}
          className="mono truncate text-xs text-text-secondary"
          title={attachment.filename}
        >
          {attachment.filename}
        </span>
        <button
          type="button"
          aria-label="Belgeyi kapat"
          onClick={onClose}
          className="rounded p-1 text-text-muted transition-colors hover:bg-raised hover:text-text-primary"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {(showWrapToggle || showExpandAll) && (
        <div className="flex flex-wrap items-center gap-2">
          {showWrapToggle && (
            <button
              type="button"
              onClick={() => setWrap((w) => !w)}
              className="btn-ghost btn-sm"
              aria-pressed={wrap}
            >
              Satır kaydırma: {wrap ? "açık" : "kapalı"}
            </button>
          )}
          {showExpandAll && (
            <button
              type="button"
              onClick={toggleAll}
              className="btn-ghost btn-sm"
              aria-pressed={allExpanded}
            >
              {allExpanded ? "Tümünü kapat" : "Tümünü aç"}
            </button>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">{body}</div>
    </Modal>
  );
}
