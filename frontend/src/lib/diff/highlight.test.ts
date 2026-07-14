/**
 * highlight.test.ts — PH-299
 *
 * Locks the pure contract of diff syntax highlighting: the extension→language
 * map, the tokenizer's plain-text fallbacks, the char-preservation invariant
 * (highlighting must never add or drop a character → diff semantics preserved),
 * and the type→color / italic palette lookups.
 *
 * Runs via Node's built-in test runner with native TS type-stripping (repo
 * convention — see grouping.test.ts, branchGraphLayout.test.ts). NO test-runner
 * dependency is added to package.json. Run:
 *   node --test --experimental-strip-types src/lib/diff/highlight.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { extToPrismLang } from "./language.ts";
import { tokenizeLine, tokenColor, tokenItalic } from "./highlight.ts";

// ---------------------------------------------------------------------------
// extToPrismLang — extension → Prism language id
// ---------------------------------------------------------------------------

test("extToPrismLang maps the AC-required extensions", () => {
  assert.equal(extToPrismLang("foo.py"), "python");
  assert.equal(extToPrismLang("foo.ts"), "typescript");
  assert.equal(extToPrismLang("foo.tsx"), "tsx");
  assert.equal(extToPrismLang("foo.json"), "json");
  assert.equal(extToPrismLang("foo.css"), "css");
  assert.equal(extToPrismLang("README.md"), "markdown");
});

test("extToPrismLang uses the final path segment + is case-insensitive", () => {
  assert.equal(extToPrismLang("src/lib/diff/highlight.TS"), "typescript");
  assert.equal(extToPrismLang("a/b/c/data.JSON"), "json");
  // a dot in a directory name must not confuse extension detection
  assert.equal(extToPrismLang("some.dir/Makefile"), null);
});

test("extToPrismLang returns null for unknown ext, dotfiles, and ext-less paths", () => {
  assert.equal(extToPrismLang("foo.unknownext"), null);
  assert.equal(extToPrismLang(".gitignore"), null); // dotfile → no highlighting
  assert.equal(extToPrismLang("Dockerfile"), null); // no extension
  assert.equal(extToPrismLang(""), null);
  assert.equal(extToPrismLang(null), null);
  assert.equal(extToPrismLang(undefined), null);
});

// ---------------------------------------------------------------------------
// tokenizeLine — plain-text fallbacks
// ---------------------------------------------------------------------------

test("tokenizeLine falls back to a single plain token when lang is null", () => {
  const line = 'const x = 1; // unknown file type';
  const toks = tokenizeLine(line, null);
  assert.deepEqual(toks, [{ content: line, types: [] }]);
});

test("tokenizeLine falls back to plain for a language with no bundled grammar", () => {
  // 'cobol' is not bundled in prism-react-renderer's vendored Prism → plain.
  const line = "IDENTIFICATION DIVISION.";
  assert.deepEqual(tokenizeLine(line, "cobol"), [{ content: line, types: [] }]);
});

test("tokenizeLine returns a single empty plain token for an empty line", () => {
  assert.deepEqual(tokenizeLine("", "python"), [{ content: "", types: [] }]);
});

// ---------------------------------------------------------------------------
// tokenizeLine — real tokenization + character-preservation invariant
// ---------------------------------------------------------------------------

const SAMPLES: ReadonlyArray<{ lang: string; line: string }> = [
  { lang: "python", line: 'def foo(x): return {"a": 1}  # note' },
  { lang: "typescript", line: "const n: number = 42; // ok" },
  { lang: "tsx", line: 'const El = () => <div className="a">{x}</div>;' },
  { lang: "json", line: '{"a": 1, "b": true, "c": "str"}' },
  { lang: "css", line: ".foo { color: red; margin: 0 auto; }" },
  { lang: "markdown", line: "# Heading with **bold** and `code`" },
];

test("tokenizeLine preserves every character (concat === input) across languages", () => {
  for (const { lang, line } of SAMPLES) {
    const rebuilt = tokenizeLine(line, lang)
      .map((t) => t.content)
      .join("");
    assert.equal(rebuilt, line, `char-preservation failed for ${lang}`);
  }
});

test("tokenizeLine actually colors known languages (≥1 typed token)", () => {
  for (const { lang, line } of SAMPLES) {
    const toks = tokenizeLine(line, lang);
    const typed = toks.some((t) => t.types.length > 0);
    assert.ok(typed, `expected at least one typed token for ${lang}`);
  }
});

test("tokenizeLine classifies python keywords and json property/number", () => {
  const py = tokenizeLine("def foo(): return 1", "python");
  assert.ok(
    py.some((t) => t.types.includes("keyword")),
    "python 'def'/'return' should tokenize as keyword",
  );

  const json = tokenizeLine('{"key": 42}', "json");
  assert.ok(json.some((t) => t.types.includes("property")), "json key → property");
  assert.ok(json.some((t) => t.types.includes("number")), "json 42 → number");
});

// ---------------------------------------------------------------------------
// tokenColor / tokenItalic — palette lookups
// ---------------------------------------------------------------------------

test("tokenColor maps known types to theme CSS vars, first-match wins", () => {
  assert.equal(tokenColor(["keyword"]), "var(--state-in_review)");
  assert.equal(tokenColor(["number"]), "var(--warning)");
  assert.equal(tokenColor(["string"]), "var(--success)");
  assert.equal(tokenColor(["property"]), "var(--accent)");
  // chain: first type with a mapping wins
  assert.equal(tokenColor(["tag", "attr-name"]), "var(--accent)");
});

test("tokenColor returns null for plain / unmapped token chains", () => {
  assert.equal(tokenColor([]), null);
  assert.equal(tokenColor(["totally-unknown-type"]), null);
});

test("tokenItalic is true only for comment chains", () => {
  assert.equal(tokenItalic(["comment"]), true);
  assert.equal(tokenItalic(["keyword"]), false);
  assert.equal(tokenItalic([]), false);
});
