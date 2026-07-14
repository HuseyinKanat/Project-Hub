import { useEffect, useRef, type ReactNode } from "react";

import { onActivateKeyDown } from "@/lib/a11y";

import { FOCUSABLE_SELECTOR, nextTrapTarget } from "./focusTrap";

/**
 * Modal — PH-305 · shared accessible modal shell (primitive)
 *
 * The a11y-critical overlay that was duplicated VERBATIM in Lightbox and DocPopup
 * (overlay + native dismiss surface + focus-trap/Esc/return effect + the
 * `role="dialog"` card container). Consumers keep their OWN header (title + close
 * button) and body and pass them as `children`; the primitive owns only the
 * shared shell.
 *
 * Guarantees:
 *   • role="dialog" aria-modal="true", labelled via `label` XOR `labelledBy`
 *   • focus trap — Tab/Shift+Tab cycle stays inside the dialog card
 *     (delegates the wrap decision to the pure, unit-tested `nextTrapTarget`)
 *   • Escape + backdrop-click close (capture-phase, matching the originals)
 *   • auto-focus the FIRST focusable on open (by repo convention the close button)
 *   • focus RETURNS to the trigger element on unmount
 *
 * onClose stability (the real fix — AC2): both callers pass an inline arrow
 * (`onClose={() => setOpen(false)}`) → a NEW identity every render. The originals
 * put `onClose` in the effect deps, so every parent re-render tore down and
 * re-ran the trap effect: it re-bound the keydown listener, re-stole focus, and —
 * the actual bug — RE-captured `previouslyFocused` while the modal was open, so
 * focus-return landed on a now-unmounted in-dialog node instead of the trigger.
 * Fixed HERE via a latest-ref: `onCloseRef` is refreshed every render (no deps),
 * while the lifecycle effect runs exactly ONCE (empty deps) and calls
 * `onCloseRef.current()`. Consumers no longer need `useCallback` for correctness.
 *
 * Sizing is passed through `className` (appended to the card); a `max-h-[92vh]`
 * cap is baked in (both current adopters share it). The dismiss surface is a
 * SIBLING outside the card with `tabIndex={-1}`, so `FOCUSABLE_SELECTOR` excludes
 * it from the trap (S6847/S6848 native-button dismiss pattern preserved).
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * ## Adoption ledger (AC3) — `grep "adopt ui/Modal"`
 *
 * Kademeli adoption: the shared shell is a11y-critical, so every remaining
 * hand-made `role="dialog"` call site migrates onto <Modal> in its own follow-up
 * ticket. Inventory: 13 non-comment `role="dialog"` sites; the 2 below are
 * migrated by PH-305, leaving **11 remaining**. Check a box when a site adopts.
 *
 * Migrated (PH-305):
 *   [x] components/attachments/Lightbox.tsx    — image evidence viewer
 *   [x] components/attachments/DocPopup.tsx    — text/doc evidence viewer
 *
 * Remaining (11 call sites — each its own follow-up ticket; adopt ui/Modal):
 *   [ ] components/AddMemberModal.tsx
 *   [ ] components/NewTicketDialog.tsx
 *   [ ] components/WorkflowList.tsx            (×2 — create + edit dialogs)
 *   [ ] components/WorkflowStateList.tsx
 *   [ ] components/repository/DetachConfirmModal.tsx
 *   [ ] components/repository/RotateSecretModal.tsx
 *   [ ] pages/BoardSettings.tsx
 *   [ ] pages/TicketDetail.tsx                 (×2 — delete + diff) — OWNED BY
 *       PH-306; do NOT edit TicketDetail from this ticket (keeps globs disjoint)
 *   [ ] components/SonarIssueDrawer.tsx        — DEFERRED: right-side DRAWER, not
 *       centered; needs an optional layout `variant` before it can adopt <Modal>
 *
 * (components/repository/RemoveRepoConfirmModal.tsx has no real dialog element —
 * its `role="dialog"` is comment-only — so it is intentionally NOT listed.)
 * ─────────────────────────────────────────────────────────────────────────────
 */
export function Modal({
  onClose,
  label,
  labelledBy,
  className,
  children,
}: Readonly<{
  /** Called on Escape, backdrop click, or the close control. Stabilized internally. */
  onClose: () => void;
  /** aria-label — pass this XOR `labelledBy` (a free-text accessible name). */
  label?: string;
  /** aria-labelledby — id of an element inside `children` that titles the dialog. */
  labelledBy?: string;
  /** Extra classes appended to the dialog card (sizing, e.g. `max-w-3xl`). */
  className?: string;
  /** Consumer-owned header + body. */
  children: ReactNode;
}>) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // latest-ref: keep the newest onClose WITHOUT re-running the lifecycle effect,
  // so an inline `onClose` (new identity each render) never re-binds the listener
  // or re-captures the trigger. Refreshed every render, no deps.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  if (import.meta.env.DEV && label === undefined && labelledBy === undefined) {
    // An unlabelled dialog is an a11y defect — surface it in dev only.
    console.warn(
      "Modal: pass exactly one of `label` or `labelledBy` — the dialog is unlabelled.",
    );
  }

  // Lifecycle effect — EMPTY deps: runs once on mount, cleans up once on unmount.
  // Captures the trigger, focuses the first control, binds a capture-phase keydown
  // (Esc close + Tab trap), and returns focus to the trigger on close.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    dialogRef.current
      ?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)
      ?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusables = Array.from(
        dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      );
      const target = nextTrapTarget(
        focusables,
        document.activeElement as HTMLElement | null,
        e.shiftKey,
      );
      if (target) {
        e.preventDefault();
        target.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      // Return focus to the element that opened the modal (the trigger).
      previouslyFocused?.focus?.();
    };
    // Mount-once: onClose reached via onCloseRef; refs are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--bg-overlay)", backdropFilter: "blur(4px)" }}
    >
      {/* Dismiss surface — a native button keeps click-outside keyboard-operable
          without a handler on a non-interactive element (S6847/S6848). Sibling
          outside the card + tabIndex={-1}, so the focus-trap query excludes it. */}
      <button
        type="button"
        aria-label="Kapat"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        onClick={() => onCloseRef.current()}
        onKeyDown={onActivateKeyDown(() => onCloseRef.current())}
      />
      <div
        ref={dialogRef}
        className={`card relative z-10 flex max-h-[92vh] flex-col gap-3 p-4${
          className ? ` ${className}` : ""
        }`}
        style={{ boxShadow: "var(--shadow-glass)" }}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-labelledby={labelledBy}
      >
        {children}
      </div>
    </div>
  );
}
