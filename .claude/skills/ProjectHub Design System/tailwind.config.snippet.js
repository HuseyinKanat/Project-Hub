/* =====================================================================
   ProjectHub Design System — "Cyan on Black"
   Tailwind theme.extend mirror of colors_and_type.css.
   darkMode: "class" — the system is dark-first; apply on <html class="dark">.
   ===================================================================== */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx,js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          base:    "#05070A",
          surface: "#0A0E14",
          raised:  "#111722",
          inset:   "#070B11",
        },
        hairline: {
          DEFAULT: "#1B2430",
          strong:  "#28323F",
          cyan:    "rgba(34,211,238,0.18)",
        },
        text: {
          primary:   "#E6EDF3",
          secondary: "#9FB0C0",
          muted:     "#5C6B7A",
        },
        accent: {
          DEFAULT: "#22D3EE",
          hover:   "#38DEF6",
          strong:  "#06B6D4",
          active:  "#0891B2",
          soft:    "rgba(34,211,238,0.12)",
          subtle:  "rgba(34,211,238,0.06)",
        },
        success: "#34D399",
        warning: "#FBBF24",
        danger:  "#F87171",
        info:    "#38BDF8",
        // Git lane palette (branch graph)
        lane: {
          cyan: "#22D3EE", teal: "#2DD4BF", sky: "#38BDF8", violet: "#A78BFA",
          amber: "#FBBF24", rose: "#FB7185", emerald: "#34D399", indigo: "#818CF8",
        },
        // Workflow state colors
        state: {
          backlog: "#94A3B8", to_do: "#38BDF8", in_progress: "#FBBF24",
          blocked: "#F87171", in_review: "#A78BFA", in_test: "#FB923C", done: "#34D399",
        },
        // Role colors
        role: {
          admin: "#94A3B8", pm: "#38BDF8", architect: "#A78BFA", backend: "#34D399",
          frontend: "#22D3EE", reviewer: "#FBBF24", qa: "#FB7185", orchestrator: "#818CF8",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", "1.4"], xs: ["12px", "1.4"], sm: ["13px", "1.5"],
        base: ["14px", "1.55"], md: ["16px", "1.5"], lg: ["18px", "1.35"],
        xl: ["22px", "1.2"], "2xl": ["28px", "1.15"], "3xl": ["36px", "1.1"],
      },
      borderRadius: {
        sm: "6px", md: "10px", lg: "14px", xl: "20px", pill: "9999px",
      },
      boxShadow: {
        sm: "0 1px 2px rgba(0,0,0,0.4)",
        md: "0 4px 16px -4px rgba(0,0,0,0.55)",
        lg: "0 16px 48px -12px rgba(0,0,0,0.7)",
        glass: "0 24px 64px -16px rgba(0,0,0,0.8)",
        "glow-cyan": "0 0 0 1px rgba(34,211,238,0.35), 0 0 22px -6px rgba(34,211,238,0.55)",
        "glow-cyan-sm": "0 0 12px -4px rgba(34,211,238,0.5)",
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0.16,1,0.3,1)",
      },
      transitionDuration: {
        fast: "120ms", base: "160ms", slow: "220ms",
      },
    },
  },
  plugins: [],
};
