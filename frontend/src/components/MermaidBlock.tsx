import { useEffect, useId, useMemo, useRef, useState } from "react";
import mermaid from "mermaid";

import { useTheme } from "@/components/ThemeProvider";

interface MermaidBlockProps {
  code: string;
}

const MONO_FONT = "'JetBrains Mono', ui-monospace, 'SF Mono', monospace";

/** Build mermaid `themeVariables` from the F1 Cyan-on-Black tokens for the given
 *  app theme. The token values live on `<html>` (`:root` dark default, `html.light`
 *  override — the selector only matches the <html> element, so a detached probe
 *  can't carry them). To resolve the TARGET theme deterministically — without a
 *  one-frame-stale palette when the effect runs before ThemeProvider's class flip
 *  lands (architect risk (b)) — we briefly force the `.light` class on <html> to
 *  match `theme`, read the computed tokens, then restore. The read is synchronous
 *  (getComputedStyle forces a style flush) so no flash is painted. Mermaid needs
 *  concrete colors, not `var()`. */
function buildThemeVariables(theme: "light" | "dark"): Record<string, string> {
  const root = document.documentElement;
  const hadLight = root.classList.contains("light");
  const wantLight = theme === "light";
  if (wantLight !== hadLight) root.classList.toggle("light", wantLight);
  const style = getComputedStyle(root);
  const t = (name: string): string => style.getPropertyValue(name).trim();
  const vars: Record<string, string> = {
    fontFamily: MONO_FONT,
    background: t("--bg-inset"),
    primaryColor: t("--bg-raised"),
    primaryBorderColor: t("--accent"),
    primaryTextColor: t("--text-primary"),
    secondaryColor: t("--bg-surface"),
    tertiaryColor: t("--bg-base"),
    textColor: t("--text-primary"),
    lineColor: t("--text-muted"),
    nodeBorder: t("--accent"),
    mainBkg: t("--bg-raised"),
    clusterBkg: t("--bg-surface"),
    clusterBorder: t("--hairline"),
    // sequence / state extras
    actorBorder: t("--accent"),
    actorBkg: t("--bg-raised"),
    signalColor: t("--text-secondary"),
    signalTextColor: t("--text-primary"),
    labelBoxBkgColor: t("--bg-inset"),
    labelBoxBorderColor: t("--hairline"),
    labelTextColor: t("--text-primary"),
    noteBkgColor: t("--accent-soft"),
    noteTextColor: t("--text-primary"),
    noteBorderColor: t("--accent"),
  };
  // Restore the class to whatever it was (ThemeProvider owns the real flip).
  if (wantLight !== hadLight) root.classList.toggle("light", hadLight);
  return vars;
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

export function MermaidBlock({ code }: Readonly<MermaidBlockProps>) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const { theme } = useTheme();
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
        // Re-initialize per render (idempotent) so the diagram picks up the
        // current app theme's Cyan-on-Black palette. `theme` is a dep below, so
        // a live theme flip re-runs this effect and re-renders the same diagram
        // into the other palette. `base`/`dark` are the mermaid substrates; the
        // token-derived themeVariables override them with the cyan accent in
        // both. buildThemeVariables(theme) resolves tokens for the TARGET theme
        // deterministically (briefly forcing+restoring the `.light` class), so
        // the palette is correct regardless of when ThemeProvider's class flip
        // lands — no one-frame-stale palette.
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          fontFamily: MONO_FONT,
          theme: theme === "light" ? "base" : "dark",
          themeVariables: buildThemeVariables(theme),
        });
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
  }, [code, isPlaceholder, theme]);

  if (isPlaceholder) {
    return (
      <div className="rounded border border-dashed border-hairline bg-inset p-3 text-xs text-text-muted">
        <div className="mb-1 font-medium text-text-secondary">Mermaid placeholder</div>
        <pre className="whitespace-pre-wrap font-mono">{code.trim() || "(boş)"}</pre>
        <p className="mt-2 italic">Architect bu bloğu henüz doldurmadı.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded border border-hairline bg-danger-soft p-3 text-xs text-danger">
        <div className="mb-1 font-medium">Mermaid hata</div>
        <pre className="whitespace-pre-wrap">{error}</pre>
        <pre className="mt-2 text-text-muted">{code}</pre>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="overflow-x-auto rounded border border-hairline bg-inset p-4 [&_svg]:mx-auto [&_svg]:max-w-full"
    />
  );
}
