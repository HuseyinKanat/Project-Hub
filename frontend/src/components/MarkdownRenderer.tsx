import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
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
            "overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100",
            compact && "p-2 text-[10px]"
          )}
        >
          {children}
        </pre>
      ),
      code: ({
        inline,
        className: codeClassName,
        children,
      }: {
        inline?: boolean;
        className?: string;
        children?: React.ReactNode;
      }) => {
        const lang = /language-(\w+)/.exec(codeClassName ?? "")?.[1];
        if (!inline && lang === "mermaid") {
          return <MermaidBlock code={String(children).replace(/\n$/, "")} />;
        }
        return inline ? (
          <code
            className={cn(
              "rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-800",
              compact && "text-[10px]"
            )}
          >
            {children}
          </code>
        ) : (
          <code className="font-mono text-xs">{children}</code>
        );
      },
      // Style links with security
      a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-600 hover:text-blue-800 hover:underline"
        >
          {children}
        </a>
      ),
      // Style headings
      h1: ({ children }: { children?: React.ReactNode }) => (
        <h1
          className={cn(
            "font-semibold text-slate-900",
            compact ? "text-sm" : "text-base"
          )}
        >
          {children}
        </h1>
      ),
      h2: ({ children }: { children?: React.ReactNode }) => (
        <h2
          className={cn(
            "font-semibold text-slate-800",
            compact ? "text-xs" : "text-sm"
          )}
        >
          {children}
        </h2>
      ),
      h3: ({ children }: { children?: React.ReactNode }) => (
        <h3
          className={cn(
            "font-medium text-slate-700",
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
            "list-disc space-y-0.5 pl-4 text-slate-700",
            compact && "text-[11px]"
          )}
        >
          {children}
        </ul>
      ),
      ol: ({ children }: { children?: React.ReactNode }) => (
        <ol
          className={cn(
            "list-decimal space-y-0.5 pl-4 text-slate-700",
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
        <thead className="bg-slate-50">{children}</thead>
      ),
      th: ({ children }: { children?: React.ReactNode }) => (
        <th className="border border-slate-200 px-2 py-1 text-left font-medium text-slate-700">
          {children}
        </th>
      ),
      td: ({ children }: { children?: React.ReactNode }) => (
        <td className="border border-slate-200 px-2 py-1 text-slate-600">
          {children}
        </td>
      ),
      // Style blockquotes
      blockquote: ({ children }: { children?: React.ReactNode }) => (
        <blockquote className="border-l-2 border-slate-300 pl-3 italic text-slate-600">
          {children}
        </blockquote>
      ),
      // Style paragraphs
      p: ({ children }: { children?: React.ReactNode }) => (
        <p
          className={cn(
            "text-slate-700 leading-relaxed",
            compact ? "text-[11px]" : "text-xs"
          )}
        >
          {children}
        </p>
      ),
      // Style horizontal rule
      hr: () => <hr className="my-2 border-slate-200" />,
    }),
    [compact]
  );

  if (!content || content.trim().length === 0) {
    return (
      <span className="text-xs italic text-slate-400">
        İçerik yok — düzenlemek için tıklayın
      </span>
    );
  }

  return (
    <div
      className={cn(
        "markdown-content prose prose-slate max-w-none",
        compact && "prose-sm",
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
