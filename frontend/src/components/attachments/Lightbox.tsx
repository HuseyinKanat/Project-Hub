import { X } from "lucide-react";

import { Modal } from "@/components/ui/Modal";

/**
 * Lightbox — PH-297 (modal shell shared via ui/Modal since PH-305)
 *
 * Accessible modal image viewer for evidence screenshots. The a11y-critical shell
 * — overlay + native dismiss surface + focus-trap/Esc/backdrop/focus-return +
 * role="dialog" card — now lives in the shared `ui/Modal` primitive, which also
 * fixes the unstable-onClose focus-return bug (PH-305 AC2). This component keeps
 * only its own header (alt caption + close button) and the <img> body.
 *
 * External props {src, alt, onClose} are UNCHANGED — callers (AttachmentItem) need
 * no edit.
 */
export function Lightbox({
  src,
  alt,
  onClose,
}: Readonly<{ src: string; alt: string; onClose: () => void }>) {
  return (
    <Modal onClose={onClose} label={alt} className="max-w-[92vw]">
      <div className="flex items-center justify-between gap-3">
        <span className="mono truncate text-xs text-text-secondary">{alt}</span>
        <button
          type="button"
          aria-label="Önizlemeyi kapat"
          onClick={onClose}
          className="rounded p-1 text-text-muted transition-colors hover:bg-raised hover:text-text-primary"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <img
        src={src}
        alt={alt}
        className="max-h-[82vh] max-w-full rounded-md object-contain"
      />
    </Modal>
  );
}
