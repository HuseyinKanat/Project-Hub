import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export const STATE_CATEGORIES: Record<string, string> = {
  backlog: "bg-slate-100 text-slate-700 ring-slate-200",
  to_do: "bg-blue-50 text-blue-700 ring-blue-200",
  in_progress: "bg-yellow-50 text-yellow-700 ring-yellow-200",
  blocked: "bg-red-50 text-red-700 ring-red-200",
  in_review: "bg-purple-50 text-purple-700 ring-purple-200",
  in_test: "bg-orange-50 text-orange-700 ring-orange-200",
  done: "bg-green-50 text-green-700 ring-green-200",
};

export const PRIORITY_DOT: Record<string, string> = {
  low: "bg-slate-400",
  medium: "bg-blue-500",
  high: "bg-orange-500",
  urgent: "bg-red-500",
};

export const TYPE_BADGE: Record<string, string> = {
  feature: "bg-emerald-100 text-emerald-700",
  bug: "bg-rose-100 text-rose-700",
  task: "bg-sky-100 text-sky-700",
  epic: "bg-violet-100 text-violet-700",
};
