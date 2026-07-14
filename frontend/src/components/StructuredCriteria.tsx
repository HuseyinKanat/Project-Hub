/**
 * StructuredCriteria.tsx — PH-301 (plan:ph-ui-readability P4)
 *
 * Read-view renderer for the structured AC/TC cards produced by
 * `lib/criteria/parseCriteria`. Purely presentational: it receives already-parsed
 * cards and never touches the edit flow (MarkdownFieldEditor renders it only in
 * the non-editing view, and only when parseCriteria returned a non-null result).
 *
 * a11y: each criterion is a self-contained <article>; clause roles are conveyed
 * with TEXT labels (`.eyebrow`), never colour alone; steps use a semantic <ol>;
 * a checklist item's [x]/[ ] state is mirrored with an accessible label.
 */

import type { CriteriaCard } from "@/lib/criteria/parseCriteria";
import { cn } from "@/lib/utils";

interface StructuredCriteriaProps {
  cards: CriteriaCard[];
}

/** Body lines of a plain (non-GWT) AC block: raw minus heading, bullets stripped. */
function plainBody(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .slice(1) // drop the heading line
    .map((l) => l.replace(/^\s*[-*]\s+/, "").trim())
    .filter((l) => l.length > 0);
}

function IdBadge({ card }: Readonly<{ card: CriteriaCard }>) {
  const isAc = card.kind === "ac";
  return (
    <span
      className={cn(
        "badge shrink-0 font-mono text-[10px] font-semibold uppercase tracking-wide",
        isAc ? "bg-accent-soft text-accent" : "bg-info-soft text-info",
      )}
    >
      {card.id}
    </span>
  );
}

function CheckState({ checked }: Readonly<{ checked: boolean }>) {
  return (
    <span
      role="img"
      aria-label={checked ? "Tamamlandı" : "Bekliyor"}
      className={cn(
        "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] leading-none",
        checked
          ? "border-success bg-success-soft text-success"
          : "border-hairline text-text-muted",
      )}
    >
      {checked ? "✓" : ""}
    </span>
  );
}

/** A labelled clause group (Given / When / Then). Renders nothing when empty. */
function ClauseRow({ label, items }: Readonly<{ label: string; items: string[] }>) {
  if (items.length === 0) return null;
  return (
    <div className="flex gap-2">
      <span className="eyebrow w-12 shrink-0 pt-0.5">{label}</span>
      <div className="flex-1 space-y-0.5">
        {items.map((item, i) => (
          <p key={`${label}-${i}`} className="text-xs leading-relaxed text-text-secondary">
            {item}
          </p>
        ))}
      </div>
    </div>
  );
}

function AcCard({ card }: Readonly<{ card: CriteriaCard }>) {
  const hasGwt = card.given.length > 0 || card.when.length > 0 || card.then.length > 0;
  const body = hasGwt ? [] : plainBody(card.raw);

  return (
    <article className="space-y-2 rounded-md border border-hairline bg-inset px-3 py-2.5">
      <div className="flex items-start gap-2">
        {card.checked !== undefined && <CheckState checked={card.checked} />}
        <IdBadge card={card} />
        {card.title && (
          <span className="flex-1 text-xs font-medium leading-snug text-text-primary">
            {card.title}
          </span>
        )}
      </div>

      {hasGwt ? (
        <div className="space-y-1.5 pl-1">
          <ClauseRow label="Given" items={card.given} />
          <ClauseRow label="When" items={card.when} />
          <ClauseRow label="Then" items={card.then} />
        </div>
      ) : (
        body.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-xs leading-relaxed text-text-secondary">
            {body.map((line, i) => (
              <li key={`body-${i}`}>{line}</li>
            ))}
          </ul>
        )
      )}
    </article>
  );
}

function TcSection({ label, children }: Readonly<{ label: string; children: React.ReactNode }>) {
  return (
    <div className="flex gap-2">
      <span className="eyebrow w-16 shrink-0 pt-0.5">{label}</span>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function TcCard({ card }: Readonly<{ card: CriteriaCard }>) {
  return (
    <article className="space-y-2 rounded-md border border-hairline bg-inset px-3 py-2.5">
      <div className="flex items-start gap-2">
        <IdBadge card={card} />
        {card.title && (
          <span className="flex-1 text-xs font-medium leading-snug text-text-primary">
            {card.title}
          </span>
        )}
      </div>

      <div className="space-y-1.5 pl-1">
        {card.preconditions.length > 0 && (
          <TcSection label="Önkoşul">
            <ul className="list-disc space-y-0.5 pl-4 text-xs leading-relaxed text-text-secondary">
              {card.preconditions.map((p, i) => (
                <li key={`pre-${i}`}>{p}</li>
              ))}
            </ul>
          </TcSection>
        )}
        {card.steps.length > 0 && (
          <TcSection label="Adımlar">
            <ol className="list-decimal space-y-0.5 pl-4 text-xs leading-relaxed text-text-secondary">
              {card.steps.map((s, i) => (
                <li key={`step-${i}`}>{s}</li>
              ))}
            </ol>
          </TcSection>
        )}
        {card.expected && (
          <TcSection label="Beklenen">
            <p className="text-xs leading-relaxed text-text-secondary">{card.expected}</p>
          </TcSection>
        )}
      </div>
    </article>
  );
}

export function StructuredCriteria({ cards }: Readonly<StructuredCriteriaProps>) {
  if (cards.length === 0) return null;
  return (
    <div className="space-y-2.5">
      {cards.map((card, i) =>
        card.kind === "ac" ? (
          <AcCard key={`${card.kind}-${card.id}-${i}`} card={card} />
        ) : (
          <TcCard key={`${card.kind}-${card.id}-${i}`} card={card} />
        ),
      )}
    </div>
  );
}
