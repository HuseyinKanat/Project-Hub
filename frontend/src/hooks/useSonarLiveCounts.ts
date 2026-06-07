/**
 * useSonarLiveCounts — PH-218: reconcile the SonarHealthPanel tile counts
 * (BUGS / VULNS / SMELLS) with the LIVE drill-down drawer so they can never
 * diverge.
 *
 * The PH-204 drawer reads issue counts LIVE from `GET .../sonarqube/issues`
 * (`SonarIssuesResponse.total`), whereas the panel tiles historically read the
 * poller-cached `board.health` snapshot (refreshed only every 5 min). Between a
 * scan and the next poll tick the panel could show a non-zero count while the
 * live drawer list was empty — a "count says N but list is empty" mismatch.
 *
 * This hook derives the three counts from the SAME source the drawer uses (its
 * `total`), fetched cheaply with `page_size: 1` (one payload row; the backend
 * returns the true `total` independent of `page_size`). Three parallel light
 * `useQueries` — one per issue type — each independently cached/deduped by
 * TanStack Query.
 *
 * GRACEFUL FALLBACK (Risk R3 / AC-2): the consumer reconciles each tile count
 * as `liveCounts.counts[type] ?? health[field]`, so while a live fetch is
 * loading OR errored (SonarQube unreachable) the panel falls back to the cached
 * `board.health` count and never renders blank.
 *
 * LIVE UPDATE (AC-3): the query keys sit in the `['board', boardKey,
 * 'sonar-issues', ...]` family, so the `sonarqube_synced` WS invalidation in
 * BoardDetail refetches these counts alongside `board.health`.
 *
 * `retry: false` mirrors `useSonarIssues` (PH-203 is graceful-200: a non-`ok`
 * `status` is a successful HTTP response carrying an error reason, not a thrown
 * error; the query only rejects on a real network failure).
 */
import { useQueries } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { SonarIssueType } from "@/types/api";

/** The three issue types whose live `total` backs the panel tiles. */
const COUNT_TYPES: readonly SonarIssueType[] = [
  "BUG",
  "VULNERABILITY",
  "CODE_SMELL",
];

export interface SonarLiveCounts {
  /**
   * Per-type live `total`. A value is `undefined` while its query is loading or
   * errored — consumers reconcile with `?? board.health.<field>` so a missing
   * live value falls back to the cached count (never blank).
   */
  counts: Partial<Record<SonarIssueType, number>>;
  /** True while ANY of the three count queries is still loading. */
  isLoading: boolean;
  /** True if ANY of the three count queries errored (real network failure). */
  isError: boolean;
}

/**
 * Fetch the live per-type issue `total` for BUG / VULNERABILITY / CODE_SMELL.
 *
 * @param boardKey board key (e.g. "PH"); queries are disabled while falsy.
 */
export function useSonarLiveCounts(boardKey: string): SonarLiveCounts {
  const results = useQueries({
    queries: COUNT_TYPES.map((type) => ({
      // Same `sonar-issues` family as useSonarIssues (so the `sonarqube_synced`
      // invalidation catches it) but a dedicated `'count'` segment so a
      // `page_size: 1` count fetch never collides with the drawer's `page`
      // list cache (Risk R2). We read `total` only — never `issues.length`.
      queryKey: ["board", boardKey, "sonar-issues", "count", type] as const,
      queryFn: () => api.getSonarIssues(boardKey, { type, page_size: 1 }),
      enabled: Boolean(boardKey),
      staleTime: 30_000,
      retry: false, // graceful-200: errors are status-coded, not thrown
    })),
  });

  const counts: Partial<Record<SonarIssueType, number>> = {};
  let isLoading = false;
  let isError = false;
  COUNT_TYPES.forEach((type, index) => {
    const result = results[index];
    if (!result) return;
    if (result.isLoading) isLoading = true;
    if (result.isError) isError = true;
    // Only surface a live count once the query has resolved successfully. A
    // graceful-200 error reason still resolves (status != 'ok' → total 0);
    // that's a legitimate live "0" and is preferred over the cached count.
    if (result.data) counts[type] = result.data.total;
  });

  return { counts, isLoading, isError };
}
