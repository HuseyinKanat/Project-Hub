/**
 * DiffDemo.tsx — dev-only demo route for DiffViewer.
 * Route: /dev/diff-demo
 * G9 / PH-158
 *
 * Contains:
 *   1. Hardcoded FileDiff[] samples (text add/del/ctx, large, binary, truncated, empty)
 *   2. Live fetch form (boardKey + sha → DiffViewer fetch mode)
 *
 * This route is intentionally available only behind RequireAuth (token required for live fetch).
 * May be removed or gated behind import.meta.env.DEV in G14.
 */
import { useState } from "react";

import type { FileDiff } from "@/types/git";
import { DiffViewer } from "@/components/diff/DiffViewer";
import { ThemeToggle } from "@/components/ThemeToggle";

// ---------------------------------------------------------------------------
// Sample data — AC-1/AC-2 patch (text add/del/ctx)
// ---------------------------------------------------------------------------

const SAMPLE_TEXT_PATCH = `@@ -1,3 +1,4 @@
 a
-b
+B
+B2
 c`;

const SAMPLE_LARGE_PATCH_LINES = Array.from({ length: 62 }, (_, i) => {
  if (i === 0) return "@@ -1,62 +1,62 @@";
  if (i % 5 === 0) return `-old line ${i}`;
  if (i % 7 === 0) return `+new line ${i}`;
  return ` context line ${i}`;
}).join("\n");

const SAMPLE_RENAME_PATCH = `diff --git a/old-name.ts b/new-name.ts
rename from old-name.ts
rename to new-name.ts
similarity index 100%`;

const SAMPLE_NO_NEWLINE_PATCH = `@@ -1 +1 @@
-a
+b
\\ No newline at end of file`;

// ---------------------------------------------------------------------------
// Sample FileDiff array
// ---------------------------------------------------------------------------

const SAMPLE_FILES: FileDiff[] = [
  // 1. Normal text file with add/del/ctx (AC-1, AC-2)
  {
    path: "src/example/hello.ts",
    old_path: null,
    change_type: "M",
    additions: 2,
    deletions: 1,
    is_binary: false,
    patch: SAMPLE_TEXT_PATCH,
    truncated: false,
  },
  // 2. New file (added) with multiple hunks
  {
    path: "src/example/new-feature.ts",
    old_path: null,
    change_type: "A",
    additions: 8,
    deletions: 0,
    is_binary: false,
    patch: `@@ -0,0 +1,4 @@
+import { foo } from './foo';
+
+export function newFeature() {
+  return foo();
@@ -0,0 +6,4 @@
+export function helper() {
+  return 'helper';
+}
+// end`,
    truncated: false,
  },
  // 3. Large hunk (>50 lines) → default collapsed (AC-4)
  {
    path: "src/example/large-file.ts",
    old_path: null,
    change_type: "M",
    additions: 20,
    deletions: 15,
    is_binary: false,
    patch: SAMPLE_LARGE_PATCH_LINES,
    truncated: false,
  },
  // 4. Binary file (AC-5)
  {
    path: "assets/image.png",
    old_path: null,
    change_type: "M",
    additions: 0,
    deletions: 0,
    is_binary: true,
    patch: null,
    truncated: false,
  },
  // 5. Truncated (AC-6)
  {
    path: "src/example/very-large.ts",
    old_path: null,
    change_type: "M",
    additions: 0,
    deletions: 0,
    is_binary: false,
    patch: null,
    truncated: true,
  },
  // 6. Rename (R)
  {
    path: "src/example/new-name.ts",
    old_path: "src/example/old-name.ts",
    change_type: "R",
    additions: 0,
    deletions: 0,
    is_binary: false,
    patch: SAMPLE_RENAME_PATCH,
    truncated: false,
  },
  // 7. Deleted file
  {
    path: "src/example/removed.ts",
    old_path: null,
    change_type: "D",
    additions: 0,
    deletions: 5,
    is_binary: false,
    patch: `@@ -1,5 +0,0 @@
-import { x } from 'x';
-
-export function removed() {
-  return x();
-}`,
    truncated: false,
  },
  // 8. No-newline-at-EOF edge case
  {
    path: "src/example/no-newline.ts",
    old_path: null,
    change_type: "M",
    additions: 1,
    deletions: 1,
    is_binary: false,
    patch: SAMPLE_NO_NEWLINE_PATCH,
    truncated: false,
  },
];

const EMPTY_FILES: FileDiff[] = [];

// ---------------------------------------------------------------------------
// Live fetch form state
// ---------------------------------------------------------------------------

interface FetchFormState {
  boardKey: string;
  sha: string;
}

// ---------------------------------------------------------------------------
// Section wrapper
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-base font-semibold text-slate-800 dark:text-slate-200 border-b border-slate-200 dark:border-slate-700 pb-2">
        {title}
      </h2>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function DiffDemoPage() {
  const [fetchForm, setFetchForm] = useState<FetchFormState>({
    boardKey: "PH",
    sha: "",
  });
  const [activeFetch, setActiveFetch] = useState<FetchFormState | null>(null);

  const handleFetchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (fetchForm.boardKey.trim() && fetchForm.sha.trim()) {
      setActiveFetch({ ...fetchForm });
    }
  };

  return (
    <div className="flex flex-col gap-8 pb-16">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">
            DiffViewer — Dev Demo
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            G9 / PH-158 — browser verify route. All edge cases rendered below.
          </p>
        </div>
        <ThemeToggle />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* 1. Normal hardcoded diff (AC-1, AC-2, AC-3, AC-4, AC-5, AC-6)      */}
      {/* ------------------------------------------------------------------ */}
      <Section title="1. Hardcoded FileDiff[] — all edge cases">
        <DiffViewer files={SAMPLE_FILES} collapseThreshold={50} />
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* 2. Empty files array (AC-9)                                         */}
      {/* ------------------------------------------------------------------ */}
      <Section title="2. Empty diff (AC-9 — no files)">
        <DiffViewer files={EMPTY_FILES} />
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* 3. Live fetch form (AC-8)                                           */}
      {/* ------------------------------------------------------------------ */}
      <Section title="3. Live fetch — commit diff (AC-8)">
        <form
          onSubmit={handleFetchSubmit}
          className="flex flex-wrap gap-3 items-end bg-slate-50 dark:bg-slate-800/60 rounded-md p-4 border border-slate-200 dark:border-slate-700"
        >
          <div className="flex flex-col gap-1">
            <label
              htmlFor="boardKey"
              className="text-xs font-medium text-slate-600 dark:text-slate-400"
            >
              Board Key
            </label>
            <input
              id="boardKey"
              type="text"
              value={fetchForm.boardKey}
              onChange={(e) =>
                setFetchForm((f) => ({ ...f, boardKey: e.target.value }))
              }
              className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm font-mono text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 w-24"
              placeholder="PH"
              aria-label="Board key"
            />
          </div>
          <div className="flex flex-col gap-1 flex-1 min-w-48">
            <label
              htmlFor="sha"
              className="text-xs font-medium text-slate-600 dark:text-slate-400"
            >
              Commit SHA
            </label>
            <input
              id="sha"
              type="text"
              value={fetchForm.sha}
              onChange={(e) =>
                setFetchForm((f) => ({ ...f, sha: e.target.value }))
              }
              className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1.5 text-sm font-mono text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500 w-full"
              placeholder="e.g. a1b2c3d4..."
              aria-label="Commit SHA"
            />
          </div>
          <button
            type="submit"
            className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 transition-colors"
            disabled={!fetchForm.boardKey.trim() || !fetchForm.sha.trim()}
          >
            Fetch Diff
          </button>
          {activeFetch && (
            <button
              type="button"
              onClick={() => setActiveFetch(null)}
              className="rounded border border-slate-300 dark:border-slate-600 px-3 py-1.5 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              Clear
            </button>
          )}
        </form>

        {activeFetch && (
          <div className="mt-2">
            <p className="mb-2 text-xs text-slate-500 dark:text-slate-400 font-mono">
              Fetching: {activeFetch.boardKey} / {activeFetch.sha}
            </p>
            <DiffViewer
              fetch={{
                kind: "commit",
                boardKey: activeFetch.boardKey,
                sha: activeFetch.sha,
              }}
            />
          </div>
        )}
      </Section>

      {/* ------------------------------------------------------------------ */}
      {/* 4. Theme note (AC-7)                                                */}
      {/* ------------------------------------------------------------------ */}
      <Section title="4. Theme verify (AC-7)">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Use the theme toggle (top-right) to switch light ↔ dark. Verify that:
          <br />• Add lines: <code className="font-mono text-xs bg-green-50 dark:bg-green-950/30 px-1 rounded">bg-green-50 / dark:bg-green-950/30</code>
          <br />• Del lines: <code className="font-mono text-xs bg-red-50 dark:bg-red-950/30 px-1 rounded">bg-red-50 / dark:bg-red-950/30</code>
        </p>
      </Section>
    </div>
  );
}
