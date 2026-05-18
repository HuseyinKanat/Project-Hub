import { useEffect, useId, useMemo, useRef, useState } from "react";
import mermaid from "mermaid";

mermaid.initialize({
  startOnLoad: false,
  theme: "neutral",
  securityLevel: "loose",
  fontFamily: "ui-monospace, monospace",
});

interface MermaidBlockProps {
  code: string;
}

/** True if the mermaid source has no nodes — only blank lines and `%%` comments.
 *  Mermaid 10+ crashes with "Cannot read properties of null (reading 'firstChild')"
 *  on such input, so we short-circuit and show a friendlier placeholder. */
function isEmptyMermaid(code: string): boolean {
  const meaningful = code
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith("%%"));
  return meaningful.length === 0;
}

/** Auto-quote participant/actor labels that contain characters Mermaid 10's
 *  sequenceDiagram parser cannot handle unquoted: `<`, `>`, `(`, `)`, `:`, `,`.
 *  Without this, common Architect output like
 *      participant Frontend as React Frontend<br/>(Desktop App)
 *  blows up with "Cannot read properties of null (reading 'firstChild')".
 *  Idempotent: already-quoted labels are left alone.
 */
function autoQuoteParticipantLabels(code: string): string {
  const re = /^(\s*(?:participant|actor)\s+\S+\s+as\s+)(.+?)\s*$/gim;
  return code.replace(re, (_match, prefix, label) => {
    const trimmed = label.trim();
    if (trimmed.startsWith('"') && trimmed.endsWith('"')) return `${prefix}${trimmed}`;
    if (/[<>():,]/.test(trimmed)) return `${prefix}"${trimmed}"`;
    return `${prefix}${trimmed}`;
  });
}

/** Mermaid 10's HTML label tokenizer accepts `<br>` but trips on `<br/>` and
 *  `<br />` inside quoted participant labels (parser yields a null AST node
 *  on whitespace-bearing self-closing tags). Normalize to the bare form. */
function normalizeBrTags(code: string): string {
  return code.replace(/<br\s*\/>/gi, "<br>");
}

/** Full preprocessor pipeline applied before mermaid.parse/render. */
function preprocessMermaid(code: string): string {
  return normalizeBrTags(autoQuoteParticipantLabels(code));
}

export function MermaidBlock({ code }: MermaidBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  // useId() provides a stable per-mount prefix (sanitized — React's ":r0:"
  // format is valid HTML but invalid CSS selectors that mermaid's internal
  // querySelector chokes on).
  const idPrefix = useId().replace(/[^a-zA-Z0-9]/g, "");
  const isPlaceholder = useMemo(() => isEmptyMermaid(code), [code]);

  useEffect(() => {
    if (isPlaceholder) {
      setError(null);
      return;
    }
    let cancelled = false;

    // Generate a fresh id PER EFFECT INVOCATION — critical: under React 18
    // StrictMode the effect runs twice on mount; if both runs share the same
    // id, mermaid's internal querySelector races and one returns null,
    // surfacing as "Cannot read properties of null (reading 'firstChild')".
    const renderId = `mermaid-${idPrefix}-${Date.now().toString(36)}-${Math.floor(
      Math.random() * 1_000_000,
    ).toString(36)}`;

    async function render() {
      if (!containerRef.current) return;
      const normalized = preprocessMermaid(code);
      try {
        await mermaid.parse(normalized);
        const { svg } = await mermaid.render(renderId, normalized);
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg;
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          const msg =
            e instanceof Error
              ? e.message
              : typeof e === "string"
                ? e
                : "Mermaid render error";
          setError(msg);
          if (containerRef.current) containerRef.current.innerHTML = "";
        }
      }
      // NOTE: do NOT call `document.getElementById(renderId).remove()` here.
      // Mermaid embeds the renderId as an attribute on the returned <svg>
      // root; once we inject the SVG into containerRef, a stray cleanup
      // call wipes it from our own container. Mermaid 10 cleans its
      // transient render scaffolding internally — no extra cleanup needed.
    }

    render();
    return () => {
      cancelled = true;
    };
  }, [code, isPlaceholder]);

  if (isPlaceholder) {
    return (
      <div className="rounded border border-dashed border-slate-300 bg-slate-50 p-3 text-xs text-slate-500">
        <div className="mb-1 font-medium text-slate-600">Mermaid placeholder</div>
        <pre className="whitespace-pre-wrap font-mono">{code.trim() || "(boş)"}</pre>
        <p className="mt-2 italic">Architect bu bloğu henüz doldurmadı.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-700">
        <div className="mb-1 font-medium">Mermaid hata</div>
        <pre className="whitespace-pre-wrap">{error}</pre>
        <pre className="mt-2 text-slate-500">{code}</pre>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="overflow-x-auto rounded border border-slate-200 bg-white p-4 [&_svg]:mx-auto [&_svg]:max-w-full"
    />
  );
}
