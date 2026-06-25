import { Tag, X } from "lucide-react";

import { laneColor } from "@/components/git/branchGraphLayout";
import { cn, hashString } from "@/lib/utils";
import type { ConceptTag } from "@/types/api";

interface ConceptTagChipProps {
  tag: ConceptTag;
  /** When set, renders a trailing × button that calls this (detach). */
  onRemove?: () => void;
  /** Disables the × button (e.g. while a detach mutation is in flight). */
  removeDisabled?: boolean;
}

/**
 * Resolve a concept-tag's accent color (PH-276 §E — CSS-var / data-driven ONLY,
 * NO hardcoded hex in a className, NO shadcn).
 *
 * - `tag.color` set (the tag's authored hue, e.g. `#7dd3fc`) → used directly via
 *   inline `style`. Tailwind cannot generate arbitrary runtime hex classes, so an
 *   inline style is the correct vehicle for a RUNTIME data value — this is NOT a
 *   hardcoded literal, it is the server-provided color.
 * - `tag.color` null/empty → a deterministic `var(--lane-*)` token chosen by
 *   hashing the slug, so the fallback is theme-reactive (repaints on light/dark)
 *   and stable per tag.
 */
function resolveTagColor(tag: ConceptTag): string {
  const authored = tag.color?.trim();
  if (authored) return authored;
  // 8 lane tokens; pick deterministically from the slug.
  return laneColor(hashString(tag.slug) % 8);
}

/**
 * ConceptTagChip — PH-276 (epic PH-271).
 *
 * A concept-tag pill that is VISUALLY DISTINCT from a `.label-chip` on THREE axes
 * so the two are unmistakable side-by-side (the core AC):
 *   1. Shape — square-ish `rounded-[5px]` (label chips are fully-rounded pills).
 *   2. Icon — a leading lucide `Tag` glyph (label chips never have an icon).
 *   3. Color — a tinted left-border + soft `color-mix` background derived from
 *      `tag.color` (label chips are colorless).
 *
 * The color is applied via inline `style` (runtime data-driven) using either the
 * authored `tag.color` or a `var(--lane-*)` fallback — never a hardcoded hex in a
 * className. `color-mix(in srgb, …)` matches the existing ActorChip / role-chip
 * tinting convention already in the codebase.
 */
export function ConceptTagChip({
  tag,
  onRemove,
  removeDisabled,
}: Readonly<ConceptTagChipProps>) {
  const color = resolveTagColor(tag);

  return (
    <span
      className={cn(
        // square taxonomy token (NOT rounded-pill) + icon + tinted color
        "inline-flex items-center gap-1 whitespace-nowrap rounded-[5px] border px-[7px] py-0.5 text-[11px] font-medium text-text-primary",
      )}
      style={{
        borderColor: color,
        borderLeftWidth: "3px",
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
      }}
      title={tag.slug}
    >
      <Tag aria-hidden className="h-3 w-3 shrink-0" style={{ color }} />
      <span className="truncate">{tag.name}</span>
      {onRemove && (
        <button
          type="button"
          className="ml-0.5 -mr-0.5 inline-flex shrink-0 items-center rounded-sm text-text-muted transition-colors hover:text-danger disabled:opacity-50"
          aria-label={`${tag.name} tag'ini kaldır`}
          onClick={onRemove}
          disabled={removeDisabled}
        >
          <X aria-hidden className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}
