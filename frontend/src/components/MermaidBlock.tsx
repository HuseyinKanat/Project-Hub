import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

mermaid.initialize({
  startOnLoad: false,
  theme: "neutral",
  securityLevel: "loose",
  fontFamily: "ui-monospace, monospace",
});

let _idCounter = 0;

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

export function MermaidBlock({ code }: MermaidBlockProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const idRef = useRef(`mermaid-${++_idCounter}`);
  const isPlaceholder = isEmptyMermaid(code);

  useEffect(() => {
    if (isPlaceholder) {
      setError(null);
      return;
    }
    let cancelled = false;

    async function render() {
      if (!containerRef.current) return;
      try {
        // Validate first — parse throws cleanly on invalid input;
        // render() can otherwise blow up inside mermaid internals.
        await mermaid.parse(code);
        const { svg } = await mermaid.render(idRef.current, code);
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
      // mermaid.render leaves a detached element in DOM; clean up
      const stale = document.getElementById(idRef.current);
      if (stale) stale.remove();
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
