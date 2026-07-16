import { useEffect, useRef, useState } from "react";
import { Download, FileText, Film, MoreVertical, Pencil, Trash2 } from "lucide-react";

import { api } from "@/api/client";
import { humaniseRelativeTr } from "@/lib/time";
import type { AttachmentResponse } from "@/types/api";

import {
  formatBytes,
  isImage,
  isMarkdown,
  isOverCap,
  isTextLike,
  isVideo,
} from "./grouping";
import { DocPopup } from "./DocPopup";
import { Lightbox } from "./Lightbox";
import { AttachmentDeleteConfirm } from "./AttachmentDeleteConfirm";
import { AttachmentPhaseEditor } from "./AttachmentPhaseEditor";

/**
 * AttachmentItem — PH-297 / PH-310
 *
 * One evidence row: kind + source TEXT badges, filename, human size, author, date,
 * and a download link. Renders by content-type:
 *   • image/*  → a clickable thumbnail button that opens the accessible Lightbox
 *   • video/*  → a native <video controls preload="metadata"> (mp4 seeks via the
 *                backend's Range/206 support — the ?token= content URL)
 *   • text fam → a "Görüntüle" button that opens the accessible DocPopup modal
 *                (markdown → rendered prose, JSON → pretty+fold, .log/.txt → mono).
 *                PH-310 replaced PH-300's inline-expand preview with the modal.
 *   • other    → metadata + download only
 *
 * The 512-KiB cap is enforced HERE: over cap, the trigger is hidden and the row
 * points at the Download button instead (the popup never opens for a huge blob).
 *
 * PH-314: a permission-aware kebab menu adds "Fazı düzenle" (canUpdate → inline
 * `AttachmentPhaseEditor`) and "Sil" (canDelete → `AttachmentDeleteConfirm`). Both
 * controls are HIDDEN when the actor lacks the capability (RBAC mirror; the server
 * 403 is the backstop, AC6). The menu is omitted entirely when neither is granted.
 */
export function AttachmentItem({
  ticketKey,
  attachment,
  canUpdate = false,
  canDelete = false,
}: Readonly<{
  ticketKey: string;
  attachment: AttachmentResponse;
  canUpdate?: boolean;
  canDelete?: boolean;
}>) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editingPhase, setEditingPhase] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const hasActions = canUpdate || canDelete;

  // Close the actions menu on click-outside + Escape (bound ONLY while open, so an
  // idle row costs no listener). Mirrors the TicketDetail "Move to →" popover.
  useEffect(() => {
    if (!menuOpen) return;
    function onDocClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const contentUrl = api.attachmentContentUrl(ticketKey, attachment.id);
  const downloadUrl = api.attachmentContentUrl(ticketKey, attachment.id, {
    download: true,
  });
  const image = isImage(attachment.content_type);
  const video = isVideo(attachment.content_type);

  // Any non-media text-family blob is viewable in the DocPopup. `isMarkdown` is an
  // OR because a `.md` mislabelled octet-stream is not `isTextLike` but still opens
  // as markdown; DocPopup itself gives markdown routing priority over the text path.
  const docViewable =
    !image &&
    !video &&
    (isTextLike(attachment.content_type, attachment.filename) ||
      isMarkdown(attachment.content_type, attachment.filename));
  const tooBig = isOverCap(attachment);

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
        <div className="flex shrink-0 items-center gap-1">
          <a
            href={downloadUrl}
            download={attachment.filename}
            className="btn-ghost btn-sm"
            aria-label={`${attachment.filename} indir`}
          >
            <Download className="h-3.5 w-3.5" />
            İndir
          </a>

          {hasActions && (
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                onClick={() => setMenuOpen((o) => !o)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label={`${attachment.filename} işlemleri`}
                className="btn-ghost btn-sm px-1.5"
              >
                <MoreVertical className="h-3.5 w-3.5" />
              </button>

              {menuOpen && (
                <div role="menu" aria-label="Kanıt işlemleri" className="menu">
                  {canUpdate && (
                    <button
                      type="button"
                      role="menuitem"
                      className="menu-item"
                      onClick={() => {
                        setMenuOpen(false);
                        setEditingPhase(true);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                      Fazı düzenle
                    </button>
                  )}
                  {canDelete && (
                    <button
                      type="button"
                      role="menuitem"
                      className="menu-item"
                      style={{ color: "var(--danger)" }}
                      onClick={() => {
                        setMenuOpen(false);
                        setDeleteOpen(true);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      Sil
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {editingPhase && (
        <AttachmentPhaseEditor
          ticketKey={ticketKey}
          attachment={attachment}
          onClose={() => setEditingPhase(false)}
        />
      )}

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

      {docViewable &&
        (tooBig ? (
          <p className="text-[11px] text-text-muted">
            Önizleme için çok büyük ({formatBytes(attachment.size_bytes)}).
            Görüntülemek için yukarıdaki <span className="mono">İndir</span>{" "}
            düğmesini kullanın.
          </p>
        ) : (
          <div>
            <button
              type="button"
              onClick={() => setDocOpen(true)}
              className="btn-ghost btn-sm"
            >
              <FileText className="h-3.5 w-3.5" aria-hidden="true" />
              Görüntüle
            </button>
          </div>
        ))}

      {docOpen && (
        <DocPopup
          ticketKey={ticketKey}
          attachment={attachment}
          onClose={() => setDocOpen(false)}
        />
      )}

      {lightboxOpen && (
        <Lightbox
          src={contentUrl}
          alt={attachment.filename}
          onClose={() => setLightboxOpen(false)}
        />
      )}

      {deleteOpen && (
        <AttachmentDeleteConfirm
          ticketKey={ticketKey}
          attachment={attachment}
          onClose={() => setDeleteOpen(false)}
        />
      )}
    </li>
  );
}
