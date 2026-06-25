/**
 * SearchResultsPanel.tsx — PH-278 (epic PH-271, child 7/7).
 *
 * The grouped `role="listbox"` rendered beneath the global search combobox. It is
 * PURELY presentational: `GlobalSearch` owns the query + the keyboard highlight
 * index and feeds this the flattened option list + per-state flags. Results are
 * shown as TWO visually-separated groups (Tickets / Concept Tags) — NEVER a mixed
 * list (AC1). Theme = CSS-var tokens + hand-written Tailwind, no shadcn (AC5).
 */
import type { ApiRequestError } from "@/api/client";
import { ConceptTagChip } from "@/components/ConceptTagChip";
import type { ConceptTag, TicketSearchHit } from "@/types/api";

/** One flattened, keyboard-navigable option (tickets first, then tags). */
export type SearchOption =
  | { kind: "ticket"; id: string; hit: TicketSearchHit }
  | { kind: "tag"; id: string; tag: ConceptTag };

interface SearchResultsPanelProps {
  /** The listbox id (the combobox's `aria-controls` target). */
  listboxId: string;
  tickets: TicketSearchHit[];
  tags: ConceptTag[];
  activeIndex: number;
  /** Per-option DOM id (so the combobox can point `aria-activedescendant`). */
  optionId: (index: number) => string;
  isFetching: boolean;
  error: ApiRequestError | null;
  onSelect: (option: SearchOption) => void;
  /** Hover preselect → mirror keyboard highlight (so click + keyboard agree). */
  onHover: (index: number) => void;
}

export function SearchResultsPanel({
  listboxId,
  tickets,
  tags,
  activeIndex,
  optionId,
  isFetching,
  error,
  onSelect,
  onHover,
}: Readonly<SearchResultsPanelProps>) {
  const hasResults = tickets.length > 0 || tags.length > 0;
  // Index of each option in the FLATTENED list (tickets then tags) so the
  // per-group rows resolve the right `activedescendant` id + click handler.
  const ticketBase = 0;
  const tagBase = tickets.length;

  return (
    <div
      id={listboxId}
      role="listbox"
      aria-label="Arama sonuçları"
      className="absolute left-0 right-0 top-full z-50 mt-1 max-h-[70vh] overflow-y-auto rounded-md border border-hairline bg-surface py-1 shadow-lg"
    >
      {error && (
        <div
          className="mx-2 my-1 rounded px-2 py-1.5 text-xs text-danger"
          style={{ background: "var(--danger-soft)" }}
          role="alert"
        >
          {error.status === 403
            ? "Arama yetkisi yok (tag.read henüz yayılmadı)."
            : (error.message ?? "Arama başarısız.")}
        </div>
      )}

      {!error && isFetching && (
        <output
          className="block px-3 py-2 text-xs text-text-muted"
          aria-live="polite"
        >
          Yükleniyor…
        </output>
      )}

      {!error && !isFetching && !hasResults && (
        <div className="px-3 py-3 text-xs text-text-muted" aria-live="polite">
          Sonuç bulunamadı
        </div>
      )}

      {!error && tickets.length > 0 && (
        <div role="group" aria-label="Tickets">
          <div className="px-3 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-text-secondary">
            Tickets
          </div>
          <ul>
            {tickets.map((hit, i) => {
              const index = ticketBase + i;
              const active = index === activeIndex;
              return (
                <li
                  key={hit.id}
                  id={optionId(index)}
                  role="option"
                  aria-selected={active}
                  className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm ${
                    active ? "bg-accent-soft" : "hover:bg-inset"
                  }`}
                  onMouseEnter={() => onHover(index)}
                  onMouseDown={(e) => {
                    // mousedown (not click) so the input's blur-close doesn't
                    // race the selection away before it registers.
                    e.preventDefault();
                    onSelect({ kind: "ticket", id: hit.id, hit });
                  }}
                >
                  <span className="mono shrink-0 text-xs text-accent">
                    {hit.key}
                  </span>
                  <span className="flex-1 truncate text-text-primary">
                    {hit.title}
                  </span>
                  <span className="shrink-0 rounded border border-hairline px-1.5 py-0.5 text-[10px] text-text-muted">
                    {hit.board}
                  </span>
                  <span className="shrink-0 text-[10px] text-text-muted">
                    {hit.state}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {!error && tickets.length > 0 && tags.length > 0 && (
        <div className="mx-2 my-1 border-t border-hairline" aria-hidden />
      )}

      {!error && tags.length > 0 && (
        <div role="group" aria-label="Concept Tags">
          <div className="px-3 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-text-secondary">
            Concept Tags
          </div>
          <ul>
            {tags.map((tag, i) => {
              const index = tagBase + i;
              const active = index === activeIndex;
              return (
                <li
                  key={tag.id}
                  id={optionId(index)}
                  role="option"
                  aria-selected={active}
                  className={`flex cursor-pointer items-center px-3 py-1.5 ${
                    active ? "bg-accent-soft" : "hover:bg-inset"
                  }`}
                  onMouseEnter={() => onHover(index)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    onSelect({ kind: "tag", id: tag.id, tag });
                  }}
                >
                  <ConceptTagChip tag={tag} />
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
