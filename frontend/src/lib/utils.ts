import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

// Cyan-on-Black tokens. Soft-tinted bg + token text + token ring; one set per
// theme via CSS vars (no `dark:` twin needed). Signatures frozen for F2–F8.
export const STATE_CATEGORIES: Record<string, string> = {
  backlog: "bg-raised text-state-backlog ring-1 ring-hairline",
  to_do: "bg-info-soft text-state-to_do ring-1 ring-hairline",
  in_progress: "bg-warning-soft text-state-in_progress ring-1 ring-hairline",
  blocked: "bg-danger-soft text-state-blocked ring-1 ring-hairline",
  in_review: "bg-accent-soft text-state-in_review ring-1 ring-hairline",
  in_test: "bg-warning-soft text-state-in_test ring-1 ring-hairline",
  done: "bg-success-soft text-state-done ring-1 ring-hairline",
};

export const PRIORITY_DOT: Record<string, string> = {
  low: "bg-text-muted",
  medium: "bg-info",
  high: "bg-warning",
  urgent: "bg-danger",
};

export const TYPE_BADGE: Record<string, string> = {
  feature: "bg-success-soft text-success",
  bug: "bg-danger-soft text-danger",
  task: "bg-info-soft text-info",
  epic: "bg-accent-soft text-lane-violet",
};
