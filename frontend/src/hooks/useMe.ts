import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/stores/auth";
import type { MeResponse } from "@/types/api";

export function useMe() {
  const token = useAuth((s) => s.token);
  return useQuery<MeResponse>({
    queryKey: ["me"],
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
