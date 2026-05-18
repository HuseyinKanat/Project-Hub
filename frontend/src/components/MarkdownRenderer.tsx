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
            "overflow-x-auto rounded bg-slate-950 p-3 text-xs text-slate-100 dark:bg-slate-950 dark:text-slate-200",
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
              "rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-800 dark:bg-slate-700 dark:text-slate-200",
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
          className="text-blue-600 hover:text-blue-800 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
        >
          {children}
        </a>
      ),
      // Style headings
      h1: ({ children }: { children?: React.ReactNode }) => (
        <h1
          className={cn(
            "font-semibold text-slate-900 dark:text-slate-100",
            compact ? "text-sm" : "text-base"
          )}
        >
          {children}
        </h1>
      ),
      h2: ({ children }: { children?: React.ReactNode }) => (
        <h2
          className={cn(
            "font-semibold text-slate-800 dark:text-slate-200",
            compact ? "text-xs" : "text-sm"
          )}
        >
          {children}
        </h2>
      ),
      h3: ({ children }: { children?: React.ReactNode }) => (
        <h3
          className={cn(
            "font-medium text-slate-700 dark:text-slate-300",
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
            "list-disc space-y-0.5 pl-4 text-slate-700 dark:text-slate-300",
            compact && "text-[11px]"
          )}
        >
          {children}
        </ul>
      ),
      ol: ({ children }: { children?: React.ReactNode }) => (
        <ol
          className={cn(
            "list-decimal space-y-0.5 pl-4 text-slate-700 dark:text-slate-300",
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
        <thead className="bg-slate-50 dark:bg-slate-800">{children}</thead>
      ),
      th: ({ children }: { children?: React.ReactNode }) => (
        <th className="border border-slate-200 px-2 py-1 text-left font-medium text-slate-700 dark:border-slate-600 dark:text-slate-300">
          {children}
        </th>
      ),
      td: ({ children }: { children?: React.ReactNode }) => (
        <td className="border border-slate-200 px-2 py-1 text-slate-600 dark:border-slate-600 dark:text-slate-400">
          {children}
        </td>
      ),
      // Style blockquotes
      blockquote: ({ children }: { children?: React.ReactNode }) => (
        <blockquote className="border-l-2 border-slate-300 pl-3 italic text-slate-600 dark:border-slate-500 dark:text-slate-400">
          {children}
        </blockquote>
      ),
      // Style paragraphs
      p: ({ children }: { children?: React.ReactNode }) => (
        <p
          className={cn(
            "text-slate-700 leading-relaxed dark:text-slate-300",
            compact ? "text-[11px]" : "text-xs"
          )}
        >
          {children}
        </p>
      ),
      // Style horizontal rule
      hr: () => <hr className="my-2 border-slate-200 dark:border-slate-700" />,
    }),
    [compact]
  );

  if (!content || content.trim().length === 0) {
    return (
      <span className="text-xs italic text-slate-400 dark:text-slate-500">
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
