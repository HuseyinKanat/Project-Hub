import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import { onActivateKeyDown } from "@/lib/a11y";

/**
 * Lightbox — PH-297
 *
 * Accessible modal image viewer for evidence screenshots. Mirrors the repo's
 * existing modal markup (TicketDetail delete/diff dialogs: overlay + native
 * dismiss-surface button + `.card` dialog) and ADDS the three a11y guarantees the
 * ticket requires:
 *   • role="dialog" aria-modal="true"
 *   • focus trap (Tab/Shift+Tab cycle stays inside)
 *   • Escape + backdrop click close
 *   • focus RETURNS to the trigger element on close (saved on mount, restored on
 *     unmount — the thumbnail button that opened it is the active element).
 */
export function Lightbox({
  src,
  alt,
  onClose,
}: Readonly<{ src: string; alt: string; onClose: () => void }>) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

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
      // Return focus to the element that opened the lightbox (the thumbnail button).
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--bg-overlay)", backdropFilter: "blur(4px)" }}
    >
      {/* Dismiss surface — native button keeps click-outside keyboard-operable
          without a handler on a non-interactive element (S6847/S6848). */}
      <button
        type="button"
        aria-label="Önizlemeyi kapat"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
        onKeyDown={onActivateKeyDown(onClose)}
      />
      <div
        ref={dialogRef}
        className="card relative z-10 flex max-h-[92vh] max-w-[92vw] flex-col gap-3 p-4"
        style={{ boxShadow: "var(--shadow-glass)" }}
        role="dialog"
        aria-modal="true"
        aria-label={alt}
      >
        <div className="flex items-center justify-between gap-3">
          <span className="mono truncate text-xs text-text-secondary">{alt}</span>
          <button
            ref={closeRef}
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
      </div>
    </div>
  );
}
