import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import { onActivateKeyDown } from "@/lib/a11y";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { StructuredCriteria } from "@/components/StructuredCriteria";
import { parseCriteria } from "@/lib/criteria/parseCriteria";
import { PRIORITY_DOT, STATE_CATEGORIES, TYPE_BADGE, cn } from "@/lib/utils";
import type { TicketResponse } from "@/types/api";

/**
 * TicketDocView — PH-303 (plan:ph-ui-readability, Dalga C)
 *
 * A read-only "spec document" of a single ticket, presented as one continuous,
 * scrollable, print-like flow inside an accessible modal. It is a pure READ
 * surface — no edit affordances, no mutations — so a reviewer / QA can read a
 * ticket's full spec (Description → AC → Test Plan → Technical Depth → Impact
 * Analysis) without the inline field editors in the way.
 *
 * Reuse (AC "no code duplication"): the AC / Test Plan sections run through the
 * SAME `parseCriteria` + `StructuredCriteria` pipeline the inline editor's
 * read-view uses (MarkdownFieldEditor, PH-301). Content that matches the AC/TC
 * convention renders as structured cards; anything else falls back to the plain
 * `MarkdownRenderer` — so legacy / free-form tickets stay readable. Technical
 * Depth + Impact Analysis are always plain Markdown.
 *
 * a11y (modal mechanics — inline copy of attachments/Lightbox, PH-297; the P8
 * Modal primitive is intentionally NOT a dependency — council decision):
 *   • role="dialog" aria-modal="true", labelled by the ticket title
 *   • focus trap (Tab / Shift+Tab cycle stays inside the dialog)
 *   • Escape + backdrop click close
 *   • focus moves to the close button on open, and RETURNS to the trigger
 *     (the "Doküman" button) on close.
 */

type DocVariant = "criteria" | "markdown";

interface DocSection {
  key: string;
  label: string;
  value: string;
  variant: DocVariant;
}

/** The subset of ticket fields the doc renders — keeps the pure builder narrow. */
type TicketDocFields = Pick<
  TicketResponse,
  "description" | "acceptance_criteria" | "test_plan" | "technical_depth" | "impact_analysis"
>;

/**
 * Pure section selector: the ordered spec sections that actually have content.
 * Empty / whitespace-only fields are dropped entirely (their heading is never
 * emitted). No React, no DOM — exported so the ordering + skip rule can be
 * reasoned about (and unit-tested) in isolation from the modal shell.
 */
export function buildDocSections(ticket: TicketDocFields): DocSection[] {
  const candidates: Array<{ key: string; label: string; value: string | null; variant: DocVariant }> = [
    { key: "description", label: "Description", value: ticket.description, variant: "markdown" },
    { key: "acceptance_criteria", label: "Acceptance Criteria", value: ticket.acceptance_criteria, variant: "criteria" },
    { key: "test_plan", label: "Test Plan", value: ticket.test_plan, variant: "criteria" },
    { key: "technical_depth", label: "Technical Depth", value: ticket.technical_depth, variant: "markdown" },
    { key: "impact_analysis", label: "Impact Analysis", value: ticket.impact_analysis, variant: "markdown" },
  ];
  const sections: DocSection[] = [];
  for (const c of candidates) {
    if (c.value && c.value.trim().length > 0) {
      sections.push({ key: c.key, label: c.label, value: c.value, variant: c.variant });
    }
  }
  return sections;
}

/** One document section: structured AC/TC cards when the content parses, else
 *  plain Markdown (mirrors the MarkdownFieldEditor read-view branch exactly). */
function DocSectionBlock({ section }: Readonly<{ section: DocSection }>) {
  const cards = section.variant === "criteria" ? parseCriteria(section.value) : null;
  return (
    <section className="space-y-2">
      <h3 className="border-b border-hairline pb-1 text-sm font-semibold text-text-primary">
        {section.label}
      </h3>
      {cards ? (
        <StructuredCriteria cards={cards} />
      ) : (
        <MarkdownRenderer content={section.value} />
      )}
    </section>
  );
}

/** The künye (spec masthead): key + type + state + priority chips, title, and
 *  the reporter / assignee line. */
function DocMasthead({ ticket, titleId }: Readonly<{ ticket: TicketResponse; titleId: string }>) {
  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="key-chip">{ticket.key}</span>
        <span className={cn("type-chip", TYPE_BADGE[ticket.type])}>{ticket.type}</span>
        <span className={cn("badge mono", STATE_CATEGORIES[ticket.state])}>{ticket.state}</span>
        <span className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
          <span className={cn("h-2 w-2 rounded-full", PRIORITY_DOT[ticket.priority])} />
          {ticket.priority}
        </span>
      </div>
      <h2
        id={titleId}
        className="text-lg font-semibold leading-snug tracking-[-0.01em] text-text-primary"
      >
        {ticket.title}
      </h2>
      <p className="mt-1 text-xs text-text-muted">
        Reporter: <span className="text-text-secondary">{ticket.reporter.display_name}</span>
        {ticket.assignee && (
          <>
            {" · "}Assignee: <span className="text-text-secondary">{ticket.assignee.display_name}</span>
          </>
        )}
      </p>
    </div>
  );
}

export function TicketDocView({
  ticket,
  onClose,
}: Readonly<{ ticket: TicketResponse; onClose: () => void }>) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const titleId = `ticket-doc-title-${ticket.key}`;

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
      // Return focus to the element that opened the doc (the "Doküman" button).
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

  const sections = buildDocSections(ticket);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--bg-overlay)", backdropFilter: "blur(4px)" }}
    >
      {/* Dismiss surface — native button keeps click-outside keyboard-operable
          without a handler on a non-interactive element (S6847/S6848). */}
      <button
        type="button"
        aria-label="Dokümanı kapat"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
        onKeyDown={onActivateKeyDown(onClose)}
      />
      <div
        ref={dialogRef}
        className="card relative z-10 flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden"
        style={{ boxShadow: "var(--shadow-glass)" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        {/* Masthead (fixed) */}
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-hairline px-6 py-4">
          <DocMasthead ticket={ticket} titleId={titleId} />
          <button
            ref={closeRef}
            type="button"
            aria-label="Dokümanı kapat"
            onClick={onClose}
            className="shrink-0 rounded p-1 text-text-muted transition-colors hover:bg-raised hover:text-text-primary"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Document body (scrollable, single continuous flow) */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {sections.length === 0 ? (
            <p className="text-sm italic text-text-muted">
              Bu ticket'ta gösterilecek doküman alanı yok.
            </p>
          ) : (
            <div className="space-y-6">
              {sections.map((s) => (
                <DocSectionBlock key={s.key} section={s} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
