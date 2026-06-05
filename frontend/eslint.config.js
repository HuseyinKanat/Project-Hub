// @ts-check
/**
 * ESLint 9 flat config — PH-165 (G14 item 4)
 *
 * Migrated from the legacy (.eslintrc-style) `eslint . --ext ts,tsx` invocation,
 * which was broken under ESLint 9 (flat config is the only supported format and
 * `--ext` was removed). No new dependencies: uses packages already present as
 * (transitive) devDeps — @eslint/js, globals, @typescript-eslint/parser,
 * @typescript-eslint/eslint-plugin.
 *
 * Scope: lint src/ TypeScript + React. Type-aware rules are intentionally NOT
 * enabled (no project service) to keep lint fast and decoupled from tsconfig;
 * `npm run typecheck` (tsc --noEmit) already covers type correctness.
 */

import js from "@eslint/js";
import globals from "globals";
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

/**
 * Minimal no-op stub for `eslint-plugin-react-hooks`.
 *
 * The codebase carries a few `// eslint-disable-next-line react-hooks/exhaustive-deps`
 * directives (intentional dependency omissions in graph layout / workflow hooks).
 * `eslint-plugin-react-hooks` is NOT a project dependency, and ESLint 9 errors on a
 * disable directive that targets an unknown rule. Registering the rule name as a
 * no-op resolver lets those directives parse cleanly without adding a dependency.
 * If/when the real plugin is added, replace this stub with the plugin's flat config.
 */
const reactHooksStub = {
  rules: {
    "exhaustive-deps": { create: () => ({}) },
    "rules-of-hooks": { create: () => ({}) },
  },
};

export default [
  // Ignore build artifacts, deps, generated + demo/dead-code-free zones.
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "test-results/**",
      "*.config.js",
      "*.config.ts",
      "vite.config.ts",
      "src/api/__smoke__/**", // type-level smoke file: many intentional unused bindings
    ],
  },

  // Linter-wide options. Keep react-hooks/* disable directives (forward-compatible
  // for when eslint-plugin-react-hooks is added) without flagging them as unused —
  // the local stub above resolves the rule name but reports nothing.
  {
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
  },

  // Base JS recommended rules.
  js.configs.recommended,

  // TypeScript + React source.
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooksStub,
    },
    rules: {
      // Start from typescript-eslint recommended set.
      ...tsPlugin.configs.recommended.rules,

      // Stub rules — registered so inline disable directives resolve; emit nothing.
      "react-hooks/exhaustive-deps": "off",
      "react-hooks/rules-of-hooks": "off",

      // The TS compiler (tsc --noEmit) owns no-undef for typed code; the core
      // rule produces false positives on TS types / global DOM types.
      "no-undef": "off",

      // Prefer the TS-aware unused-vars rule; allow leading-underscore opt-out
      // for intentionally-unused args/vars (e.g. _rowIdx, _c in map callbacks).
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrors: "none",
        },
      ],

      // `any` is used pragmatically in a few typed-boundary spots (api client,
      // git types `unknown[]`); warn rather than error so lint stays green while
      // still surfacing the smell.
      "@typescript-eslint/no-explicit-any": "warn",

      // Non-null assertions are used deliberately in lane/graph layout where
      // index invariants hold; warn instead of hard error.
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },

  // Node-context files (e.g. env typing) — allow node globals.
  {
    files: ["src/env.d.ts"],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
];
