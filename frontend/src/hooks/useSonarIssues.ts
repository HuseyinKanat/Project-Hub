/**
 * useSonarIssues — PH-204 (epic PH-201, D2): lazy TanStack Query hook for the
 * SonarQube issue drill-down drawer.
 *
 * LAZY by design (Risk R1): `enabled` is gated on `type != null`, so the
 * SonarHealthPanel pays ZERO network cost until a tile is actually clicked.
 * The drawer also only mounts when `openType != null`, so this hook never runs
 * during the idle panel render.
 *
 * `retry: false` because the PH-203 endpoint is graceful-200: a non-`ok`
 * `status` is a successful HTTP response carrying an error reason, NOT a thrown
 * error. The query only rejects on a real network failure — both paths funnel
 * into the drawer's single error state.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { SonarIssuesResponse, SonarIssueType } from "@/types/api";

export function useSonarIssues(
  boardKey: string,
  type: SonarIssueType | null,
  page = 1,
) {
  return useQuery<SonarIssuesResponse>({
    // Key family matches the app's ['board', boardKey, ...] convention
    // (BoardDetail.tsx, hooks/useMe.ts).
    queryKey: ["board", boardKey, "sonar-issues", type, page],
    queryFn: () => api.getSonarIssues(boardKey, { type: type!, page }),
    enabled: Boolean(boardKey) && type != null, // LAZY — no fetch until a tile opens
    staleTime: 30_000,
    retry: false, // graceful-200: errors are status-coded, not thrown
  });
}
