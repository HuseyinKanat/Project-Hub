import { createContext, useContext } from "react";
import ReactMarkdown from "react-markdown";
import type { Components, ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";
import { MermaidBlock } from "./MermaidBlock";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  compact?: boolean;
}

// `compact` is provided via context so the styled element components can be
// defined at module scope (stable identities across renders — see
// typescript:S6478) while still reacting to the parent's compact prop.
const CompactContext = createContext(false);

type ChildrenProps = Readonly<{ children?: React.ReactNode }>;

function MdPre({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <pre
      className={cn(
        "overflow-x-auto rounded border border-hairline bg-inset p-3 text-xs text-text-primary",
        compact && "p-2 text-[10px]"
      )}
    >
      {children}
    </pre>
  );
}

function MdCode({
  className: codeClassName,
  children,
}: Readonly<React.HTMLAttributes<HTMLElement> & ExtraProps>) {
  const compact = useContext(CompactContext);
  const lang = /language-(\w+)/.exec(codeClassName ?? "")?.[1];
  if (lang === "mermaid") {
    const code = String(children).replace(/\n$/, "");
    // key={code} forces remount when the diagram source changes —
    // avoids stale container state across HMR reloads and edits.
    return <MermaidBlock key={code} code={code} />;
  }
  if (codeClassName) {
    return <code className="font-mono text-xs">{children}</code>;
  }
  return (
    <code
      className={cn(
        "rounded bg-inset px-1 py-0.5 font-mono text-xs text-text-secondary",
        compact && "text-[10px]"
      )}
    >
      {children}
    </code>
  );
}

function MdAnchor({
  href,
  children,
}: Readonly<{ href?: string; children?: React.ReactNode }>) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent hover:text-accent-hover hover:underline"
    >
      {children}
    </a>
  );
}

function MdH1({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <h1
      className={cn(
        "font-semibold text-text-primary",
        compact ? "text-sm" : "text-base"
      )}
    >
      {children}
    </h1>
  );
}

function MdH2({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <h2
      className={cn(
        "font-semibold text-text-primary",
        compact ? "text-xs" : "text-sm"
      )}
    >
      {children}
    </h2>
  );
}

function MdH3({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <h3
      className={cn(
        "font-medium text-text-secondary",
        compact ? "text-[11px]" : "text-xs"
      )}
    >
      {children}
    </h3>
  );
}

function MdUl({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <ul
      className={cn(
        "list-disc space-y-0.5 pl-4 text-text-secondary",
        compact && "text-[11px]"
      )}
    >
      {children}
    </ul>
  );
}

function MdOl({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <ol
      className={cn(
        "list-decimal space-y-0.5 pl-4 text-text-secondary",
        compact && "text-[11px]"
      )}
    >
      {children}
    </ol>
  );
}

function MdTable({ children }: ChildrenProps) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-xs">{children}</table>
    </div>
  );
}

function MdThead({ children }: ChildrenProps) {
  return <thead className="bg-raised">{children}</thead>;
}

function MdTh({ children }: ChildrenProps) {
  return (
    <th className="border border-hairline px-2 py-1 text-left font-medium text-text-primary">
      {children}
    </th>
  );
}

function MdTd({ children }: ChildrenProps) {
  return (
    <td className="border border-hairline px-2 py-1 text-text-secondary">
      {children}
    </td>
  );
}

function MdBlockquote({ children }: ChildrenProps) {
  return (
    <blockquote className="border-l-2 border-hairline pl-3 italic text-text-secondary">
      {children}
    </blockquote>
  );
}

function MdParagraph({ children }: ChildrenProps) {
  const compact = useContext(CompactContext);
  return (
    <p
      className={cn(
        "text-text-secondary leading-relaxed",
        compact ? "text-[11px]" : "text-xs"
      )}
    >
      {children}
    </p>
  );
}

function MdHr() {
  return <hr className="my-2 border-hairline" />;
}

// Stable component map — defined once at module scope.
const markdownComponents: Components = {
  pre: MdPre,
  code: MdCode,
  a: MdAnchor,
  h1: MdH1,
  h2: MdH2,
  h3: MdH3,
  ul: MdUl,
  ol: MdOl,
  table: MdTable,
  thead: MdThead,
  th: MdTh,
  td: MdTd,
  blockquote: MdBlockquote,
  p: MdParagraph,
  hr: MdHr,
};

export function MarkdownRenderer({
  content,
  className,
  compact = false,
}: Readonly<MarkdownRendererProps>) {
  if (!content || content.trim().length === 0) {
    return (
      <span className="text-xs italic text-text-muted">
        İçerik yok — düzenlemek için tıklayın
      </span>
    );
  }

  return (
    <CompactContext.Provider value={compact}>
      <div className={cn("markdown-content space-y-2", className)}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {content}
        </ReactMarkdown>
      </div>
    </CompactContext.Provider>
  );
}

// Compact version for inline/card display
export function MarkdownCompact({ content }: Readonly<{ content: string }>) {
  return <MarkdownRenderer content={content} compact />;
}
