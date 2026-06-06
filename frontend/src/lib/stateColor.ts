import type { CSSProperties } from "react";
import type { WorkflowState } from "@/types/api";
import { STATE_CATEGORIES } from "@/lib/utils";

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * Canonical workflow state names that have a theme-aware `--state-<name>` token
 * defined in index.css (both dark `:root` and `html.light`). Derived from
 * STATE_CATEGORIES so the set stays in lockstep with the F1 token list — do NOT
 * hardcode a second list.
 */
const CANONICAL_STATES = new Set(Object.keys(STATE_CATEGORIES));

/**
 * Resolves a canonical state's color to its theme-aware `--state-<name>` token.
 *
 * Returns `var(--state-<name>)` when `name` is one of the 7 canonical states,
 * else `undefined`. The membership guard is REQUIRED: emitting
 * `var(--state-<unknown>)` for a non-existent token resolves to an empty value,
 * which makes `color-mix(in srgb, <empty> 7%, …)` invalid and silently breaks
 * the gradient. Only emit the token for known canonical states.
 */
export function stateTokenColor(name: string): string | undefined {
  return CANONICAL_STATES.has(name) ? `var(--state-${name})` : undefined;
}

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

  // Fallback: Tailwind class from STATE_CATEGORIES or safe token default
  return {
    className:
      STATE_CATEGORIES[state?.name ?? ""] ??
      "bg-raised text-text-secondary ring-1 ring-hairline",
  };
}
