import { useId, useMemo, useState } from "react";
import { Download, FileText, Film } from "lucide-react";

import { api, ApiRequestError } from "@/api/client";
import { humaniseRelativeTr } from "@/lib/time";
import type { AttachmentResponse } from "@/types/api";

import {
  foldJsonTopLevel,
  formatBytes,
  isImage,
  isJsonAttachment,
  isTextLike,
  isVideo,
  prettyPrintJson,
  TEXT_PREVIEW_CAP_BYTES,
} from "./grouping";
import { Lightbox } from "./Lightbox";

/**
 * AttachmentItem — PH-297
 *
 * One evidence row: kind + source TEXT badges, filename, human size, author, date,
 * and a download link. Renders inline previews by content-type:
 *   • image/*  → a clickable thumbnail button that opens the accessible Lightbox
 *   • video/*  → a native <video controls preload="metadata"> (mp4 seeks via the
 *                backend's Range/206 support — the ?token= content URL)
 *   • other    → metadata + download only
 */
export function AttachmentItem({
  ticketKey,
  attachment,
}: Readonly<{ ticketKey: string; attachment: AttachmentResponse }>) {
  const [lightboxOpen, setLightboxOpen] = useState(false);

  const contentUrl = api.attachmentContentUrl(ticketKey, attachment.id);
  const downloadUrl = api.attachmentContentUrl(ticketKey, attachment.id, {
    download: true,
  });
  const image = isImage(attachment.content_type);
  const video = isVideo(attachment.content_type);

  // PH-300 — inline text/JSON preview (only for non-media text-family blobs).
  const textLike =
    !image && !video && isTextLike(attachment.content_type, attachment.filename);
  const isJson =
    textLike && isJsonAttachment(attachment.content_type, attachment.filename);
  const tooBig = attachment.size_bytes > TEXT_PREVIEW_CAP_BYTES;

  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [wrap, setWrap] = useState(true);
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(new Set());

  async function loadText() {
    setStatus("loading");
    setErrorMsg(null);
    try {
      const t = await api.fetchAttachmentText(ticketKey, attachment.id, {
        maxBytes: TEXT_PREVIEW_CAP_BYTES,
      });
      setText(t);
      setStatus("idle");
    } catch (err) {
      setErrorMsg(
        err instanceof ApiRequestError
          ? err.status === 403
            ? "Bu eki görüntüleme yetkiniz yok (403)."
            : `Önizleme yüklenemedi: ${err.message}`
          : "Önizleme yüklenemedi.",
      );
      setStatus("error");
    }
  }

  function handleToggle() {
    const next = !open;
    setOpen(next);
    // Lazy: fetch bytes only on the first expand, then cache in state.
    if (next && text === null && status !== "loading") void loadText();
  }

  // Parse once per fetched body: a plain OBJECT root folds by top-level key; an
  // array/scalar root (or a JSON-hinted body that fails to parse) falls back to a
  // flat pretty pane / raw text.
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

  const preClass = `mono mt-2 overflow-auto rounded border border-hairline bg-raised p-2 text-[11px] leading-relaxed text-text-primary ${
    wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"
  }`;

  let leftSlot: React.ReactNode;
  if (image) {
    leftSlot = (
      <button
        type="button"
        onClick={() => setLightboxOpen(true)}
        aria-label={`${attachment.filename} önizlemesini büyüt`}
        className="shrink-0 overflow-hidden rounded border border-hairline transition-opacity hover:opacity-80"
      >
        <img
          src={contentUrl}
          alt=""
          loading="lazy"
          className="h-14 w-14 object-cover"
        />
      </button>
    );
  } else {
    const Icon = video ? Film : FileText;
    leftSlot = (
      <span
        aria-hidden="true"
        className="flex h-14 w-14 shrink-0 items-center justify-center rounded border border-hairline bg-raised text-text-muted"
      >
        <Icon className="h-5 w-5" />
      </span>
    );
  }

  return (
    <li className="flex flex-col gap-2 rounded-md border border-hairline p-2.5">
      <div className="flex items-start gap-3">
        {leftSlot}
        <div className="min-w-0 flex-1">
          <p
            className="mono truncate text-xs text-text-primary"
            title={attachment.filename}
          >
            {attachment.filename}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-muted">
            <span className="label-chip" title="Tür">
              {attachment.kind}
            </span>
            <span className="label-chip" title="Kaynak">
              {attachment.source}
            </span>
            <span>{formatBytes(attachment.size_bytes)}</span>
            <span aria-hidden="true">·</span>
            <span className="truncate">{attachment.author.display_name}</span>
            <span aria-hidden="true">·</span>
            <time dateTime={attachment.created_at}>
              {humaniseRelativeTr(attachment.created_at)}
            </time>
          </div>
        </div>
        <a
          href={downloadUrl}
          download={attachment.filename}
          className="btn-ghost btn-sm shrink-0"
          aria-label={`${attachment.filename} indir`}
        >
          <Download className="h-3.5 w-3.5" />
          İndir
        </a>
      </div>

      {video && (
        <video
          controls
          preload="metadata"
          src={contentUrl}
          aria-label={`${attachment.filename} video oynatıcı`}
          className="w-full rounded-md border border-hairline"
          style={{ maxHeight: 360 }}
        />
      )}

      {textLike && (
        <div>
          {tooBig ? (
            <p className="text-[11px] text-text-muted">
              Önizleme için çok büyük ({formatBytes(attachment.size_bytes)}).
              Görüntülemek için yukarıdaki{" "}
              <span className="mono">İndir</span> düğmesini kullanın.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={handleToggle}
                  className="btn-ghost btn-sm"
                  aria-expanded={open}
                  aria-controls={panelId}
                >
                  <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                  {open ? "Gizle" : "Görüntüle"}
                </button>

                {open && status === "idle" && text !== null && (
                  <button
                    type="button"
                    onClick={() => setWrap((w) => !w)}
                    className="btn-ghost btn-sm"
                    aria-pressed={wrap}
                  >
                    Satır kaydırma: {wrap ? "açık" : "kapalı"}
                  </button>
                )}

                {open &&
                  status === "idle" &&
                  jsonView?.kind === "fold" &&
                  foldableKeys.length > 0 && (
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

              {open && (
                <div id={panelId} className="mt-2">
                  {status === "loading" && (
                    <p className="text-[11px] text-text-muted">Yükleniyor…</p>
                  )}
                  {status === "error" && (
                    <p
                      role="alert"
                      className="rounded-md bg-danger-soft px-2 py-1.5 text-[11px] text-danger"
                    >
                      {errorMsg}
                    </p>
                  )}
                  {status === "idle" &&
                    text !== null &&
                    (jsonView?.kind === "fold" ? (
                      <ul className="flex flex-col gap-1 rounded border border-hairline bg-raised p-2">
                        {jsonView.entries.map((entry) => {
                          const expanded =
                            entry.body === null || expandedKeys.has(entry.key);
                          return (
                            <li key={entry.key} className="min-w-0">
                              {entry.body !== null ? (
                                <button
                                  type="button"
                                  onClick={() => toggleKey(entry.key)}
                                  aria-expanded={expanded}
                                  className="mono flex w-full items-center gap-1.5 text-left text-[11px] text-text-primary hover:opacity-80"
                                >
                                  <span
                                    aria-hidden="true"
                                    className="text-text-muted"
                                  >
                                    {expanded ? "▾" : "▸"}
                                  </span>
                                  <span className="shrink-0">{entry.key}</span>
                                  {!expanded && (
                                    <span className="truncate text-text-muted">
                                      {entry.summary}
                                    </span>
                                  )}
                                </button>
                              ) : (
                                <p className="mono flex items-center gap-1.5 text-[11px]">
                                  <span
                                    aria-hidden="true"
                                    className="text-text-muted"
                                  >
                                    ·
                                  </span>
                                  <span className="shrink-0 text-text-primary">
                                    {entry.key}
                                  </span>
                                  <span className="truncate text-text-muted">
                                    {entry.summary}
                                  </span>
                                </p>
                              )}
                              {entry.body !== null && expanded && (
                                <pre
                                  className={preClass}
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
                    ) : (
                      <pre
                        className={preClass}
                        tabIndex={0}
                        role="region"
                        aria-label={`${attachment.filename} içeriği`}
                        style={{ maxHeight: 360 }}
                      >
                        {jsonView?.kind === "flat" ? jsonView.pretty : text}
                      </pre>
                    ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {lightboxOpen && (
        <Lightbox
          src={contentUrl}
          alt={attachment.filename}
          onClose={() => setLightboxOpen(false)}
        />
      )}
    </li>
  );
}
