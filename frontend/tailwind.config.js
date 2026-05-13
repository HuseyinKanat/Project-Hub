/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        state: {
          backlog: "#9ca3af",
          to_do: "#3b82f6",
          in_progress: "#eab308",
          blocked: "#ef4444",
          in_review: "#a855f7",
          in_test: "#f97316",
          done: "#22c55e",
        },
      },
    },
  },
  plugins: [],
};
