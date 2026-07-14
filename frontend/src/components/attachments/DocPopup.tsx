import { useEffect, useId, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";

import { api, ApiRequestError } from "@/api/client";
import { onActivateKeyDown } from "@/lib/a11y";
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
 * DocPopup — PH-310
 *
 * Accessible modal document viewer for TEXT-family evidence + spec docs (`.md`
 * AC/test-case attachments, JSON reports, `.log`/`.txt`). It reproduces the
 * Lightbox modal SHELL verbatim — an INLINE copy so the image and document
 * viewers can evolve independently — with the three a11y guarantees:
 *   • role="dialog" aria-modal="true" aria-labelledby (the filename heading)
 *   • focus trap (Tab/Shift+Tab cycle stays inside the dialog)
 *   • Escape + backdrop click close
 *   • focus moves to the close button on open, RETURNS to the trigger on close
 *
 * Content is fetched here (self-contained loading/error/idle state, reusing
 * `api.fetchAttachmentText`) and routed by type — markdown → prose, JSON →
 * pretty + per-key fold, else → monospace `<pre>` with a line-wrap toggle. The
 * 512-KiB cap is enforced by the CALLER (AttachmentItem hides the trigger when
 * `size_bytes` is over cap and offers download instead); `maxBytes` here is a
 * defensive second belt.
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
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();

  const markdown = isMarkdown(attachment.content_type, attachment.filename);
  const isJson =
    !markdown && isJsonAttachment(attachment.content_type, attachment.filename);

  const [status, setStatus] = useState<"loading" | "error" | "idle">("loading");
  const [text, setText] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [wrap, setWrap] = useState(true);
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(new Set());

  // Focus trap + Escape/backdrop + focus-return. Verbatim from Lightbox (the repo's
  // audited modal primitive) so the document viewer inherits the same guarantees.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusables = dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
      );
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      // Return focus to the element that opened the popup (the trigger chip/button).
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--bg-overlay)", backdropFilter: "blur(4px)" }}
    >
      {/* Dismiss surface — native button keeps click-outside keyboard-operable
          without a handler on a non-interactive element (S6847/S6848). */}
      <button
        type="button"
        aria-label="Belgeyi kapat"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
        onKeyDown={onActivateKeyDown(onClose)}
      />
      <div
        ref={dialogRef}
        className="card relative z-10 flex max-h-[92vh] w-full max-w-3xl flex-col gap-3 p-4"
        style={{ boxShadow: "var(--shadow-glass)" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex items-center justify-between gap-3">
          <span
            id={titleId}
            className="mono truncate text-xs text-text-secondary"
            title={attachment.filename}
          >
            {attachment.filename}
          </span>
          <button
            ref={closeRef}
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
      </div>
    </div>
  );
}
