/**
 * GlobalSearch.tsx — PH-278 (epic PH-271, child 7/7, FINAL).
 *
 * The app-wide search affordance mounted in the `Layout` header. A debounced
 * (250ms) TanStack Query hits the cross-board `GET /api/search` (PH-275) and drops
 * a grouped result listbox (`SearchResultsPanel`) beneath the input. Ticket hits
 * navigate to the cross-board detail route (board KEY based); tag hits push the tag
 * into the shared `searchFilter` store + route to `/space`, where the chip drives
 * the PH-277 `SpaceGraph` highlight.
 *
 * A11y: the input is `role="combobox"` (`aria-expanded`/`aria-controls`/
 * `aria-activedescendant`); ↑/↓ move a highlight across the FLATTENED option list
 * (tickets then tags), Enter activates, ESC closes + clears, Tab/blur close. The
 * query is `enabled` only on a non-empty trimmed term (matches the backend blank-q
 * short-circuit) and uses `keepPreviousData` so results don't flicker to empty
 * between keystrokes (also the stale-response guard — React Query orders by key).
 *
 * Theme = CSS-var tokens + hand-written Tailwind, NO shadcn (AC5).
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useCallback, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiRequestError, api } from "@/api/client";
import {
  type SearchOption,
  SearchResultsPanel,
} from "@/components/search/SearchResultsPanel";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useSearchFilter } from "@/stores/searchFilter";

export function GlobalSearch() {
  const navigate = useNavigate();
  const addTag = useSearchFilter((s) => s.addTag);

  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  const debouncedTerm = useDebouncedValue(term, 250);
  const trimmed = debouncedTerm.trim();

  const baseId = useId();
  const listboxId = `${baseId}-listbox`;
  const optionId = useCallback(
    (index: number) => `${baseId}-opt-${index}`,
    [baseId],
  );

  const { data, isFetching, error } = useQuery({
    queryKey: ["search", trimmed],
    queryFn: () => api.searchAll(trimmed),
    enabled: trimmed.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  const tickets = useMemo(() => data?.tickets ?? [], [data]);
  const tags = useMemo(() => data?.concept_tags ?? [], [data]);

  // Flattened, keyboard-navigable option list: tickets first, then tags.
  const options = useMemo<SearchOption[]>(
    () => [
      ...tickets.map((hit) => ({ kind: "ticket" as const, id: hit.id, hit })),
      ...tags.map((tag) => ({ kind: "tag" as const, id: tag.id, tag })),
    ],
    [tickets, tags],
  );

  // Only show the panel when there is a committed term (query enabled). An empty
  // input keeps the panel closed (query disabled ⇒ nothing to show).
  const panelOpen = open && trimmed.length > 0;

  const close = useCallback(() => {
    setOpen(false);
    setActiveIndex(-1);
  }, []);

  const select = useCallback(
    (option: SearchOption) => {
      if (option.kind === "ticket") {
        // Cross-board: board is the KEY (not id) → KEY-based detail route.
        navigate(`/boards/${option.hit.board}/tickets/${option.hit.key}`);
      } else {
        // Tag hit → push into the shared filter store + go to /space, where the
        // chip drives the SpaceGraph highlight (the PH-277 seam).
        addTag(option.tag);
        navigate("/space");
      }
      setTerm("");
      close();
      inputRef.current?.blur();
    },
    [navigate, addTag, close],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setTerm("");
        close();
        inputRef.current?.blur();
        return;
      }
      if (!panelOpen || options.length === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % options.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => (i <= 0 ? options.length - 1 : i - 1));
      } else if (e.key === "Enter") {
        const chosen = options[activeIndex];
        if (chosen) {
          e.preventDefault();
          select(chosen);
        }
      }
    },
    [panelOpen, options, activeIndex, select, close],
  );

  const apiError = error instanceof ApiRequestError ? error : null;

  return (
    <div className="relative hidden sm:block sm:w-56 md:w-72">
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
        />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label="Ticket ve tag ara (cross-board)"
          aria-expanded={panelOpen}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={
            panelOpen && activeIndex >= 0 ? optionId(activeIndex) : undefined
          }
          value={term}
          placeholder="Ara…"
          onChange={(e) => {
            setTerm(e.target.value);
            setOpen(true);
            setActiveIndex(-1);
          }}
          onFocus={() => setOpen(true)}
          // Delay so a row mousedown registers before the panel closes.
          onBlur={() => setTimeout(close, 120)}
          onKeyDown={onKeyDown}
          className="w-full rounded-md border border-hairline bg-inset py-1.5 pl-8 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>

      {panelOpen && (
        <SearchResultsPanel
          listboxId={listboxId}
          tickets={tickets}
          tags={tags}
          activeIndex={activeIndex}
          optionId={optionId}
          isFetching={isFetching}
          error={apiError}
          onSelect={select}
          onHover={setActiveIndex}
        />
      )}
    </div>
  );
}
