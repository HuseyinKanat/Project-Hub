import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/stores/auth";
import type { MeResponse } from "@/types/api";

export function useMe() {
  const token = useAuth((s) => s.token);
  return useQuery<MeResponse>({
    // PH-232: token-scoped key so a token change yields a NEW cache entry and
    // can NEVER serve the prior identity's `me`. isAdmin/useBoardRole derive
    // from this, so they follow automatically on an in-app identity switch with
    // no hard reload. (queryClient.clear() in the auth store drops the orphaned
    // old-token entry immediately; the key change is the belt to clear()'s
    // braces.)
    queryKey: ["me", token],
    queryFn: () => api.getMe(),
    staleTime: 5 * 60 * 1000, // 5 minutes
    retry: false, // 401 retry is meaningless — request<T> already calls logout() on 401
    enabled: !!token,
  });
}

export function useBoardRole(boardKey: string | undefined): string | null {
  const { data: me } = useMe();
  if (!me || !boardKey) return null;
  return me.memberships.find((m) => m.board_key === boardKey)?.role ?? null;
}

/**
 * PH-332 — pure mirror of the backend `require_global_board_creator` gate
 * (deps.py): the `board.create` cap is granted only by the admin role's `*`
 * wildcard, so "can create a board" == "admin of at least one board". Exported
 * (pure, `me`-in / bool-out) so it is unit-testable without React. Undefined
 * `me` (still loading / logged out) → false, so the caller renders NOTHING
 * rather than flashing the action before eligibility resolves.
 */
export function canCreateBoard(me: MeResponse | undefined): boolean {
  return !!me?.memberships.some((m) => m.role === "admin");
}

/**
 * PH-332 — hook wrapper: true when the current actor may create a board. Gating
 * convention is HIDE-when-false (repo pattern: RepositoryList / MembersTab hide
 * admin-only actions); the POST-time 403 (AC5) is only the submit backstop.
 */
export function useCanCreateBoard(): boolean {
  const { data: me } = useMe();
  return canCreateBoard(me);
}
