/**
 * highlight.ts — line-based syntax tokenization for diff content cells (PH-299).
 *
 * Uses prism-react-renderer's vendored Prism to tokenize ONE diff line at a
 * time (the single injection point is HunkView's content cell). Because each
 * diff row is rendered independently, multi-line constructs (block comments,
 * template/heredoc strings that span lines) may mis-classify at their
 * boundaries — an accepted trade-off for a diff viewer. See technical_depth.
 *
 * Colors lean entirely on the existing design-token CSS variables (index.css:
 * dark `:root` + `html.light`), so both themes are covered for free with no
 * Prism theme object.
 */
import { Prism } from "prism-react-renderer";

/** A flat, ordered slice of a tokenized line. */
export interface FlatToken {
  content: string;
  /** Prism token type chain (outer→inner) + aliases; `[]` for plain text. */
  types: string[];
}

/** Structural view of a Prism token — avoids a hard type dep on `prismjs`. */
interface PrismToken {
  type: string;
  content: string | PrismToken | Array<string | PrismToken>;
  alias?: string | string[];
}

/** Recursively flatten Prism's nested token tree into ordered leaf segments. */
function flatten(node: string | PrismToken, inherited: string[], out: FlatToken[]): void {
  if (typeof node === "string") {
    if (node.length > 0) out.push({ content: node, types: inherited });
    return;
  }
  const alias = node.alias
    ? Array.isArray(node.alias)
      ? node.alias
      : [node.alias]
    : [];
  const types = [...inherited, node.type, ...alias];
  const { content } = node;
  if (typeof content === "string") {
    if (content.length > 0) out.push({ content, types });
  } else if (Array.isArray(content)) {
    for (const child of content) flatten(child, types, out);
  } else {
    flatten(content, types, out);
  }
}

/**
 * Tokenize a single line of code into flat, colored segments.
 *
 * Fallbacks that render identically to the pre-highlight plain cell:
 *   - `lang === null` (unknown extension)            → one plain token
 *   - grammar not bundled in the vendored Prism      → one plain token
 *   - Prism throws, or produces nothing              → one plain token
 *
 * Invariant: the concatenation of all returned `content` equals `content` in —
 * highlighting never adds or drops a character, so the rendered text is
 * byte-identical to the plain render (diff semantics preserved).
 */
export function tokenizeLine(content: string, lang: string | null): FlatToken[] {
  if (!lang || content === "") return [{ content, types: [] }];
  const grammar = Prism.languages[lang];
  if (!grammar) return [{ content, types: [] }];
  try {
    const raw = Prism.tokenize(content, grammar) as unknown as Array<string | PrismToken>;
    const out: FlatToken[] = [];
    for (const node of raw) flatten(node, [], out);
    return out.length > 0 ? out : [{ content, types: [] }];
  } catch {
    return [{ content, types: [] }];
  }
}

/**
 * Prism token type → design-token CSS variable. Leans on existing semantic
 * tokens so light/dark are covered without a bespoke palette. First matching
 * type in the chain wins; unknown types inherit the cell's default color.
 */
const TYPE_COLOR: Readonly<Record<string, string>> = {
  // keywords / language constructs — violet
  keyword: "var(--state-in_review)",
  boolean: "var(--state-in_review)",
  builtin: "var(--state-in_review)",
  important: "var(--state-in_review)",
  atrule: "var(--state-in_review)",
  // strings / inserted — green
  string: "var(--success)",
  char: "var(--success)",
  regex: "var(--success)",
  "attr-value": "var(--success)",
  inserted: "var(--success)",
  // numbers / constants — amber
  number: "var(--warning)",
  constant: "var(--warning)",
  symbol: "var(--warning)",
  // comments / prolog — muted (also italic, see tokenItalic)
  comment: "var(--text-muted)",
  prolog: "var(--text-muted)",
  doctype: "var(--text-muted)",
  cdata: "var(--text-muted)",
  // functions / types — sky
  function: "var(--info)",
  "class-name": "var(--info)",
  "function-variable": "var(--info)",
  entity: "var(--info)",
  url: "var(--info)",
  // identifiers / keys / tags — cyan accent
  property: "var(--accent)",
  tag: "var(--accent)",
  selector: "var(--accent)",
  "attr-name": "var(--accent)",
  namespace: "var(--accent)",
  // variables / deleted — red
  variable: "var(--danger)",
  deleted: "var(--danger)",
  // operators / punctuation — secondary text
  operator: "var(--text-secondary)",
  punctuation: "var(--text-secondary)",
};

/**
 * Resolve a token type chain → a theme-aware CSS color, or `null` to inherit
 * the cell's default text color. Pure.
 */
export function tokenColor(types: string[]): string | null {
  for (const t of types) {
    const c = TYPE_COLOR[t];
    if (c) return c;
  }
  return null;
}

/** Whether a token type chain should render italic (comments). Pure. */
export function tokenItalic(types: string[]): boolean {
  return types.includes("comment");
}
