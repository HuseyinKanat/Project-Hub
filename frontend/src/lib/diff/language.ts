/**
 * language.ts — file-extension → Prism language-id resolution (PH-299).
 *
 * Pure and dependency-free so it unit-tests under node:test without loading
 * Prism. The tokenizer (`highlight.ts`) guards on grammar presence, so an entry
 * whose language is NOT bundled in prism-react-renderer's vendored Prism (e.g.
 * "bash") degrades to plain text rather than throwing — the map can stay
 * generous without risking a runtime error.
 */

/** Extension (lowercase, no leading dot) → Prism language id. */
const EXT_TO_LANG: Readonly<Record<string, string>> = {
  // Python
  py: "python",
  pyi: "python",
  // TypeScript / JavaScript
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  // Data / config
  json: "json",
  jsonc: "json",
  yaml: "yaml",
  yml: "yaml",
  // Styles
  css: "css",
  // Docs
  md: "markdown",
  markdown: "markdown",
  // Markup
  html: "markup",
  htm: "markup",
  xml: "markup",
  svg: "markup",
  // Shell (grammar may be absent → plain fallback)
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  // Other common source langs bundled by prism-react-renderer
  sql: "sql",
  go: "go",
  rs: "rust",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  kt: "kotlin",
  kts: "kotlin",
  swift: "swift",
  graphql: "graphql",
  gql: "graphql",
};

/**
 * Resolve a Prism language id from a file path via its extension.
 *
 * @returns the language id, or `null` for extension-less paths, dotfiles
 *          (e.g. `.gitignore`) or unknown extensions — callers render plain.
 */
export function extToPrismLang(path: string | null | undefined): string | null {
  if (!path) return null;
  // Take the final path segment (handles both `/` and `\` separators).
  const base = path.split(/[\\/]/).pop() ?? "";
  const dot = base.lastIndexOf(".");
  // dot <= 0 covers "no extension" (-1) and dotfiles like ".gitignore" (0).
  if (dot <= 0) return null;
  const ext = base.slice(dot + 1).toLowerCase();
  return EXT_TO_LANG[ext] ?? null;
}
