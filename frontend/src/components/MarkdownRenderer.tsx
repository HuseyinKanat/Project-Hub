import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import type { ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";
import { MermaidBlock } from "./MermaidBlock";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  compact?: boolean;
}

export function MarkdownRenderer({
  content,
  className,
  compact = false,
}: MarkdownRendererProps) {
  const components = useMemo(
    () => ({
      // Style code blocks
      pre: ({ children }: { children?: React.ReactNode }) => (
        <pre
          className={cn(
            "overflow-x-auto rounded border border-hairline bg-inset p-3 text-xs text-text-primary",
            compact && "p-2 text-[10px]"
          )}
        >
          {children}
        </pre>
      ),
      code: ({
        className: codeClassName,
        children,
      }: React.HTMLAttributes<HTMLElement> & ExtraProps) => {
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
      },
      // Style links with security
      a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent hover:text-accent-hover hover:underline"
        >
          {children}
        </a>
      ),
      // Style headings
      h1: ({ children }: { children?: React.ReactNode }) => (
        <h1
          className={cn(
            "font-semibold text-text-primary",
            compact ? "text-sm" : "text-base"
          )}
        >
          {children}
        </h1>
      ),
      h2: ({ children }: { children?: React.ReactNode }) => (
        <h2
          className={cn(
            "font-semibold text-text-primary",
            compact ? "text-xs" : "text-sm"
          )}
        >
          {children}
        </h2>
      ),
      h3: ({ children }: { children?: React.ReactNode }) => (
        <h3
          className={cn(
            "font-medium text-text-secondary",
            compact ? "text-[11px]" : "text-xs"
          )}
        >
          {children}
        </h3>
      ),
      // Style lists
      ul: ({ children }: { children?: React.ReactNode }) => (
        <ul
          className={cn(
            "list-disc space-y-0.5 pl-4 text-text-secondary",
            compact && "text-[11px]"
          )}
        >
          {children}
        </ul>
      ),
      ol: ({ children }: { children?: React.ReactNode }) => (
        <ol
          className={cn(
            "list-decimal space-y-0.5 pl-4 text-text-secondary",
            compact && "text-[11px]"
          )}
        >
          {children}
        </ol>
      ),
      // Style tables (GitHub flavored)
      table: ({ children }: { children?: React.ReactNode }) => (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-xs">
            {children}
          </table>
        </div>
      ),
      thead: ({ children }: { children?: React.ReactNode }) => (
        <thead className="bg-raised">{children}</thead>
      ),
      th: ({ children }: { children?: React.ReactNode }) => (
        <th className="border border-hairline px-2 py-1 text-left font-medium text-text-primary">
          {children}
        </th>
      ),
      td: ({ children }: { children?: React.ReactNode }) => (
        <td className="border border-hairline px-2 py-1 text-text-secondary">
          {children}
        </td>
      ),
      // Style blockquotes
      blockquote: ({ children }: { children?: React.ReactNode }) => (
        <blockquote className="border-l-2 border-hairline pl-3 italic text-text-secondary">
          {children}
        </blockquote>
      ),
      // Style paragraphs
      p: ({ children }: { children?: React.ReactNode }) => (
        <p
          className={cn(
            "text-text-secondary leading-relaxed",
            compact ? "text-[11px]" : "text-xs"
          )}
        >
          {children}
        </p>
      ),
      // Style horizontal rule
      hr: () => <hr className="my-2 border-hairline" />,
    }),
    [compact]
  );

  if (!content || content.trim().length === 0) {
    return (
      <span className="text-xs italic text-text-muted">
        İçerik yok — düzenlemek için tıklayın
      </span>
    );
  }

  return (
    <div
      className={cn(
        "markdown-content space-y-2",
        className
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

// Compact version for inline/card display
export function MarkdownCompact({ content }: { content: string }) {
  return <MarkdownRenderer content={content} compact />;
}
