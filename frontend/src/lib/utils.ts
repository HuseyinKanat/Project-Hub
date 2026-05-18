import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export const STATE_CATEGORIES: Record<string, string> = {
  backlog: "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
  to_do: "bg-blue-50 text-blue-700 ring-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:ring-blue-900",
  in_progress: "bg-yellow-50 text-yellow-700 ring-yellow-200 dark:bg-yellow-950/40 dark:text-yellow-300 dark:ring-yellow-900",
  blocked: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900",
  in_review: "bg-purple-50 text-purple-700 ring-purple-200 dark:bg-purple-950/40 dark:text-purple-300 dark:ring-purple-900",
  in_test: "bg-orange-50 text-orange-700 ring-orange-200 dark:bg-orange-950/40 dark:text-orange-300 dark:ring-orange-900",
  done: "bg-green-50 text-green-700 ring-green-200 dark:bg-green-950/40 dark:text-green-300 dark:ring-green-900",
};

export const PRIORITY_DOT: Record<string, string> = {
  low: "bg-slate-400",
  medium: "bg-blue-500",
  high: "bg-orange-500",
  urgent: "bg-red-500",
};

export const TYPE_BADGE: Record<string, string> = {
  feature: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  bug: "bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300",
  task: "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  epic: "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
};
