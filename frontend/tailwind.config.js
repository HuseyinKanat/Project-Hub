/** @type {import('tailwindcss').Config} */
export default {
  // Tokens carry both themes via CSS vars; `dark:` is a last-resort escape hatch.
  // Dark is the default (:root); `html.light` overrides. The custom selector keeps
  // any residual `dark:` utility resolving under the dark-default convention.
  darkMode: ["selector", "html:not(.light)"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // ---- Colors: every semantic key aliases a CSS var (no baked hexes) ----
      colors: {
        base: "var(--bg-base)",
        surface: "var(--bg-surface)",
        raised: "var(--bg-raised)",
        inset: "var(--bg-inset)",
        overlay: "var(--bg-overlay)",
        hairline: {
          DEFAULT: "var(--hairline)",
          strong: "var(--hairline-strong)",
          cyan: "var(--hairline-cyan)",
        },
        text: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          muted: "var(--text-muted)",
          "on-accent": "var(--text-on-accent)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          strong: "var(--accent-strong)",
          active: "var(--accent-active)",
          soft: "var(--accent-soft)",
          subtle: "var(--accent-subtle)",
          ring: "var(--accent-ring)",
        },
        success: { DEFAULT: "var(--success)", soft: "var(--success-soft)" },
        warning: { DEFAULT: "var(--warning)", soft: "var(--warning-soft)" },
        danger: { DEFAULT: "var(--danger)", soft: "var(--danger-soft)" },
        info: { DEFAULT: "var(--info)", soft: "var(--info-soft)" },
        state: {
          backlog: "var(--state-backlog)",
          to_do: "var(--state-to_do)",
          in_progress: "var(--state-in_progress)",
          blocked: "var(--state-blocked)",
          in_review: "var(--state-in_review)",
          in_test: "var(--state-in_test)",
          done: "var(--state-done)",
        },
        role: {
          admin: "var(--role-admin)",
          pm: "var(--role-pm)",
          architect: "var(--role-architect)",
          backend: "var(--role-backend)",
          frontend: "var(--role-frontend)",
          reviewer: "var(--role-reviewer)",
          qa: "var(--role-qa)",
          orchestrator: "var(--role-orchestrator)",
        },
        lane: {
          cyan: "var(--lane-cyan)",
          teal: "var(--lane-teal)",
          sky: "var(--lane-sky)",
          violet: "var(--lane-violet)",
          amber: "var(--lane-amber)",
          rose: "var(--lane-rose)",
          emerald: "var(--lane-emerald)",
          indigo: "var(--lane-indigo)",
        },
      },
      // ---- Type families ----
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SF Mono", "Cascadia Code", "monospace"],
      },
      // ---- Type scale (mirrors the skill token scale) ----
      fontSize: {
        "2xs": ["10px", { lineHeight: "1.4" }],
        xs: ["12px", { lineHeight: "1.4" }],
        sm: ["13px", { lineHeight: "1.5" }],
        base: ["14px", { lineHeight: "1.55" }],
        md: ["16px", { lineHeight: "1.55" }],
        lg: ["18px", { lineHeight: "1.35" }],
        xl: ["22px", { lineHeight: "1.2" }],
        "2xl": ["28px", { lineHeight: "1.2" }],
        "3xl": ["36px", { lineHeight: "1.1" }],
      },
      // ---- Radii ----
      borderRadius: {
        sm: "6px",
        md: "10px",
        lg: "14px",
        xl: "20px",
        pill: "9999px",
      },
      // ---- Letter spacing ----
      letterSpacing: {
        wide: "0.04em",
        tight: "-0.01em",
      },
      // ---- Elevation + cyan glow (var-driven so light swaps them) ----
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        glass: "var(--shadow-glass)",
        "glow-cyan": "var(--glow-cyan)",
        "glow-cyan-sm": "var(--glow-cyan-sm)",
      },
      // ---- Motion ----
      transitionTimingFunction: {
        out: "cubic-bezier(0.16, 1, 0.3, 1)",
        standard: "cubic-bezier(0.4, 0, 0.2, 1)",
      },
      transitionDuration: {
        fast: "120ms",
        base: "160ms",
        slow: "220ms",
      },
    },
  },
  plugins: [],
};
