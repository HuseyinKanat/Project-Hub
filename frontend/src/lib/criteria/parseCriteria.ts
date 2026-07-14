/**
 * parseCriteria.ts — PH-301 (plan:ph-ui-readability P4)
 *
 * Pure, deterministic parser that turns the free-text `acceptance_criteria` /
 * `test_plan` markdown fields into structured AC/TC cards for a read-view
 * renderer (StructuredCriteria). This is a progressive-enhancement DETECTOR —
 * NOT a hard schema: content that does not match the AC/TC convention returns
 * `null`, and the caller falls back to the plain MarkdownRenderer so existing
 * tickets never break. The backend schema is unchanged (FE-only convention).
 *
 * Runs both in the browser and under Node's test runner (node:test + native TS
 * strip) — the same colocated-`.test.ts` pattern as attachments/grouping.ts and
 * identityGuard.ts. Kept import-free so `--experimental-strip-types` can load it
 * with no alias resolution.
 *
 * Recognised shapes
 * -----------------
 * GWT acceptance criteria (heading form — council-canonical):
 *   ### AC1: <title>
 *   - GIVEN <context>
 *   - WHEN  <action>
 *   - THEN  <expected>
 *   - AND   <extra>            (attaches to the previous clause)
 *
 * GWT acceptance criteria (checklist form — as emitted by older tickets):
 *   - [ ] GIVEN <context> WHEN <action> THEN <expected>
 *   - [x] AC2: GIVEN <context> WHEN <action> THEN <expected>
 *
 * Test cases (heading form, EN + TR keywords):
 *   ### TC1: <title>
 *   - Önkoşul / Precondition: <precondition>
 *   - Adımlar / Steps:
 *     1. <step>
 *     2. <step>
 *   - Beklenen / Expected: <expected>
 *
 * A `### AC\d+` block with no GWT bullets is still a (plain) AC card; only
 * content with NO AC and NO TC block at all yields `null`.
 */

export type CriteriaKind = "ac" | "tc";

export interface CriteriaCard {
  kind: CriteriaKind;
  /** e.g. "AC1" / "TC2"; synthesised sequentially for unnumbered checklist ACs. */
  id: string;
  title: string;
  given: string[];
  when: string[];
  then: string[];
  preconditions: string[];
  steps: string[];
  expected: string;
  /** Original block/line text — lets a plain-AC card show its body verbatim. */
  raw: string;
  /** Checklist state ([x]/[ ]); undefined for heading-form cards. */
  checked?: boolean;
}

// `#`..`####` heading introducing an AC/TC block, capturing id + trailing title.
const HEADING_RE = /^#{1,4}\s+((?:AC|TC)[-\s]?\d+)\b[.:]?\s*(.*)$/i;
// A single Gherkin clause bullet inside a heading block.
const GWT_LINE_RE = /^\s*[-*]?\s*(GIVEN|WHEN|THEN|AND|BUT)\b\s*[-:]?\s*(.*)$/i;
// Any Gherkin keyword anywhere in a line (checklist single-line form).
const GWT_KEYWORD_RE = /\b(GIVEN|WHEN|THEN|AND|BUT)\b/gi;
// GFM task-list item, capturing its checked mark + remaining content.
const CHECKLIST_RE = /^\s*[-*]\s*\[([ xX])\]\s*(.*)$/;
// Optional "AC3:" / "AC3." / "AC3)" id prefix inside a checklist item.
const AC_PREFIX_RE = /^(AC[-\s]?\d+)\s*[:.)]\s*(.*)$/i;
// A numbered step ("1." / "1)").
const NUMBERED_STEP_RE = /^(\d+)[.)]\s+(.*)$/;
// A generic "Label: value" line (used to detect TC section labels).
const LABELLED_LINE_RE = /^[-*]?\s*([^:]+?)\s*:\s*(.*)$/;
// A plain bullet ("- x" / "* x").
const BULLET_RE = /^[-*]\s+(.*)$/;

type ClauseBucket = "given" | "when" | "then";
type TcSection = "pre" | "steps" | "expected";

/** Normalise a captured id ("ac 1", "TC-2") to a compact upper form ("AC1"). */
function normaliseId(raw: string): string {
  return raw.toUpperCase().replace(/[-\s]+/g, "");
}

function emptyCard(
  kind: CriteriaKind,
  id: string,
  title: string,
  raw: string,
): CriteriaCard {
  return {
    kind,
    id,
    title,
    given: [],
    when: [],
    then: [],
    preconditions: [],
    steps: [],
    expected: "",
    raw,
  };
}

/**
 * Split a single line that may pack a whole GIVEN/WHEN/THEN scenario onto one
 * row (the checklist form) into its clause buckets. Text before the first
 * keyword becomes the title. AND/BUT extend whichever clause preceded them.
 */
function splitInlineGwt(text: string): {
  given: string[];
  when: string[];
  then: string[];
  title: string;
} {
  const given: string[] = [];
  const when: string[] = [];
  const then: string[] = [];
  const matches = [...text.matchAll(GWT_KEYWORD_RE)];
  if (matches.length === 0) {
    return { given, when, then, title: text.trim() };
  }

  const firstAt = matches[0]?.index ?? 0;
  const title = text
    .slice(0, firstAt)
    .trim()
    .replace(/[:\-–—]\s*$/, "")
    .trim();

  let last: ClauseBucket | null = null;
  for (let i = 0; i < matches.length; i += 1) {
    const m = matches[i];
    if (!m) continue;
    const keyword = (m[1] ?? "").toUpperCase();
    const start = (m.index ?? 0) + (m[0] ?? "").length;
    const next = matches[i + 1];
    const end = next ? next.index ?? text.length : text.length;
    const clause = text
      .slice(start, end)
      .trim()
      .replace(/[,;.]\s*$/, "")
      .trim();

    if (keyword === "GIVEN") {
      if (clause) given.push(clause);
      last = "given";
    } else if (keyword === "WHEN") {
      if (clause) when.push(clause);
      last = "when";
    } else if (keyword === "THEN") {
      if (clause) then.push(clause);
      last = "then";
    } else if (clause && last) {
      // AND / BUT — continuation of the previous clause bucket.
      if (last === "given") given.push(clause);
      else if (last === "when") when.push(clause);
      else then.push(clause);
    }
  }
  return { given, when, then, title };
}

/** Fill a heading-form AC card's clauses from its body bullet lines. */
function fillAcClauses(bodyLines: string[], card: CriteriaCard): void {
  let last: ClauseBucket | null = null;
  for (const line of bodyLines) {
    const m = GWT_LINE_RE.exec(line);
    if (!m) continue;
    const keyword = (m[1] ?? "").toUpperCase();
    const clause = (m[2] ?? "").trim();
    if (keyword === "GIVEN") {
      if (clause) card.given.push(clause);
      last = "given";
    } else if (keyword === "WHEN") {
      if (clause) card.when.push(clause);
      last = "when";
    } else if (keyword === "THEN") {
      if (clause) card.then.push(clause);
      last = "then";
    } else if (clause && last) {
      card[last].push(clause);
    }
  }
}

function buildAcCard(
  id: string,
  title: string,
  bodyLines: string[],
  raw: string,
): CriteriaCard {
  const card = emptyCard("ac", id, title, raw);
  fillAcClauses(bodyLines, card);
  // Fallback: a heading block whose Gherkin sits on one line (no per-line
  // bullets) still parses via the inline splitter.
  if (card.given.length === 0 && card.then.length === 0) {
    const inline = splitInlineGwt(bodyLines.join(" "));
    card.given = inline.given;
    card.when = inline.when;
    card.then = inline.then;
  }
  return card;
}

/** Classify a TC section label token (EN + TR) into a canonical section. */
function classifyTcLabel(label: string): TcSection | null {
  const l = label.trim().toLowerCase();
  if (/^(ön ?ko|on ?ko|precondition|prerequisite)/.test(l)) return "pre";
  if (/^(ad[ıi]m|step)/.test(l)) return "steps";
  if (/^(beklenen|expected|result|sonu[cç])/.test(l)) return "expected";
  return null;
}

function pushTc(
  card: CriteriaCard,
  expectedParts: string[],
  section: TcSection,
  value: string,
): void {
  if (!value) return;
  if (section === "pre") card.preconditions.push(value);
  else if (section === "steps") card.steps.push(value);
  else expectedParts.push(value);
}

function buildTcCard(
  id: string,
  title: string,
  bodyLines: string[],
  raw: string,
): CriteriaCard {
  const card = emptyCard("tc", id, title, raw);
  const expectedParts: string[] = [];
  let section: TcSection | null = null;

  for (const line of bodyLines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    const numbered = NUMBERED_STEP_RE.exec(trimmed);
    if (numbered) {
      const step = (numbered[2] ?? "").trim();
      if (step) card.steps.push(step);
      section = "steps";
      continue;
    }

    const labelled = LABELLED_LINE_RE.exec(trimmed);
    if (labelled) {
      const cls = classifyTcLabel(labelled[1] ?? "");
      if (cls) {
        section = cls;
        pushTc(card, expectedParts, cls, (labelled[2] ?? "").trim());
        continue;
      }
    }

    // Bare bullet / continuation line under the active section.
    if (section) {
      const bullet = BULLET_RE.exec(trimmed);
      pushTc(card, expectedParts, section, (bullet ? bullet[1] ?? "" : trimmed).trim());
    }
  }

  card.expected = expectedParts.join(" ").trim();
  return card;
}

/** Parse checklist-form ACs (only used when no heading blocks matched). */
function parseChecklistAc(lines: string[]): CriteriaCard[] {
  // Pre-scan: reserve every explicit AC id (e.g. "AC1:") up front so the
  // implicit auto-counter can never mint an id a user already authored —
  // regardless of whether the explicit item precedes or follows the id-less
  // one. Without this, an id-less GWT item and an explicit "AC1:" item both
  // resolve to "AC1" and StructuredCriteria renders duplicate badges.
  const reserved = new Set<string>();
  for (const line of lines) {
    const cl = CHECKLIST_RE.exec(line);
    if (!cl) continue;
    const idMatch = AC_PREFIX_RE.exec((cl[2] ?? "").trim());
    if (idMatch) reserved.add(normaliseId(idMatch[1] ?? ""));
  }

  const cards: CriteriaCard[] = [];
  const assigned = new Set<string>();
  let auto = 0;
  for (const line of lines) {
    const cl = CHECKLIST_RE.exec(line);
    if (!cl) continue;

    const checked = (cl[1] ?? "").toLowerCase() === "x";
    let content = (cl[2] ?? "").trim();
    if (!content) continue;

    let id: string | null = null;
    const idMatch = AC_PREFIX_RE.exec(content);
    if (idMatch) {
      id = normaliseId(idMatch[1] ?? "");
      content = (idMatch[2] ?? "").trim();
    }

    const inline = splitInlineGwt(content);
    const hasGwt = inline.given.length >= 1 && inline.then.length >= 1;
    // A plain checklist item ("- [ ] do the thing") is NOT an AC — it only
    // qualifies with inline GWT or an explicit AC-id prefix.
    if (!hasGwt && id === null) continue;

    let cardId = id;
    if (cardId === null) {
      // Advance past any id already reserved by an explicit prefix or already
      // emitted, so synthesised ids stay unique in mixed checklists.
      auto += 1;
      while (reserved.has(`AC${auto}`) || assigned.has(`AC${auto}`)) auto += 1;
      cardId = `AC${auto}`;
    }
    assigned.add(cardId);

    const card = emptyCard("ac", cardId, hasGwt ? inline.title : content, line.trim());
    if (hasGwt) {
      card.given = inline.given;
      card.when = inline.when;
      card.then = inline.then;
    }
    card.checked = checked;
    cards.push(card);
  }
  return cards;
}

interface PendingBlock {
  kind: CriteriaKind;
  id: string;
  title: string;
  headLine: string;
  body: string[];
}

/**
 * Parse the markdown content of an `acceptance_criteria` / `test_plan` field
 * into structured cards, or `null` when it matches no AC/TC convention.
 */
export function parseCriteria(
  content: string | null | undefined,
): CriteriaCard[] | null {
  if (!content || content.trim().length === 0) return null;
  const lines = content.split(/\r?\n/);

  // Pass 1 — heading-form AC/TC blocks.
  const cards: CriteriaCard[] = [];
  let current: PendingBlock | null = null;

  const flush = (): void => {
    if (!current) return;
    const raw = [current.headLine, ...current.body].join("\n").trim();
    cards.push(
      current.kind === "ac"
        ? buildAcCard(current.id, current.title, current.body, raw)
        : buildTcCard(current.id, current.title, current.body, raw),
    );
    current = null;
  };

  for (const line of lines) {
    const heading = HEADING_RE.exec(line);
    if (heading) {
      flush();
      const id = normaliseId(heading[1] ?? "");
      current = {
        kind: id.startsWith("AC") ? "ac" : "tc",
        id,
        title: (heading[2] ?? "").trim(),
        headLine: line,
        body: [],
      };
    } else if (current) {
      current.body.push(line);
    }
  }
  flush();

  if (cards.length > 0) return cards;

  // Pass 2 — checklist-form ACs (only when no heading blocks were present).
  const checklist = parseChecklistAc(lines);
  return checklist.length > 0 ? checklist : null;
}
