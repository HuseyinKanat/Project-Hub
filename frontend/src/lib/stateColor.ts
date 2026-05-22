import type { CSSProperties } from "react";
import type { WorkflowState } from "@/types/api";
import { STATE_CATEGORIES } from "@/lib/utils";

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * Resolves the visual tone for a workflow state.
 *
 * Priority:
 *   1. state.color is a valid 6-char hex → return inline CSSProperties
 *      (background at 10% alpha, border at 30% alpha, text at full saturation)
 *   2. STATE_CATEGORIES[state.name] exists → return that Tailwind className
 *   3. Both missing → return safe slate fallback className
 *
 * Call sites pass the full WorkflowState object (or undefined when the
 * ticket.state string has no matching state in board.workflow.states).
 */
export function resolveStateColor(state: WorkflowState | undefined): {
  style?: CSSProperties;
  className?: string;
} {
  const hex = state?.color;

  if (hex && HEX_RE.test(hex)) {
    return {
      style: {
        backgroundColor: hex + "1A", // ~10 % alpha
        borderColor: hex + "4D",     // ~30 % alpha
        color: hex,
      },
    };
  }

  // Fallback: Tailwind class from STATE_CATEGORIES or safe default
  return {
    className:
      STATE_CATEGORIES[state?.name ?? ""] ??
      "bg-slate-50 text-slate-700 ring-slate-200 dark:bg-slate-800/50 dark:text-slate-300 dark:ring-slate-700",
  };
}
