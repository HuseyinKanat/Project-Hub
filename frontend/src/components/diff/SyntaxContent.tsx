/**
 * SyntaxContent.tsx — renders one diff line's content with Prism-based syntax
 * highlighting (PH-299).
 *
 * Drop-in replacement for the bare `{line.content}` text node in HunkView's
 * content cell. It preserves `whitespace-pre` semantics (the parent `<td>` owns
 * the class) and renders byte-identical text — only wrapping colored/italic
 * segments in `<span>`; plain segments emit a keyed Fragment (no extra DOM
 * node), keeping the tree light for 500+ line hunks. Memoized so unrelated
 * re-renders (collapse toggles, hover) never re-tokenize.
 */
import { memo, Fragment } from "react";
import type { CSSProperties, ReactNode } from "react";

import { tokenizeLine, tokenColor, tokenItalic } from "@/lib/diff/highlight";

interface SyntaxContentProps {
  content: string;
  /** Prism language id, or `null` → render plain text (no tokenization). */
  lang: string | null;
}

function SyntaxContentImpl({ content, lang }: Readonly<SyntaxContentProps>) {
  // Fast path: no language (unknown ext / meta line) → plain, no Prism work.
  if (lang === null) return <>{content}</>;

  const tokens = tokenizeLine(content, lang);
  const nodes: ReactNode[] = tokens.map((tok, i) => {
    const color = tokenColor(tok.types);
    const italic = tokenItalic(tok.types);
    if (!color && !italic) {
      // Plain segment — keyed Fragment renders text with zero extra DOM.
      return <Fragment key={i}>{tok.content}</Fragment>;
    }
    const style: CSSProperties = {};
    if (color) style.color = color;
    if (italic) style.fontStyle = "italic";
    return (
      <span key={i} style={style}>
        {tok.content}
      </span>
    );
  });

  return <>{nodes}</>;
}

export const SyntaxContent = memo(SyntaxContentImpl);
