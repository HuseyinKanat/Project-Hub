import { QueryClient } from "@tanstack/react-query";

/**
 * The single TanStack Query client for the whole app (PH-232).
 *
 * Lifted out of `main.tsx` into its own module so that NON-React code — in
 * particular the Zustand auth store (`stores/auth.ts`), which runs outside the
 * React tree and therefore cannot use `useQueryClient()` — can import the SAME
 * singleton and call `queryClient.clear()` at an identity boundary.
 *
 * Why a dedicated module instead of importing from `main.tsx`: `main.tsx`
 * transitively imports `<App/>` → the auth store, so having the store import
 * back from `main.tsx` would form an import cycle. A leaf module that BOTH
 * `main.tsx` and the store import from has no cycle.
 *
 * SECURITY (PH-232): a token switch is a trust boundary. `auth.ts` calls
 * `queryClient.clear()` on every real identity change (login / logout / the two
 * outside-React 401 auto-logouts that route through `logout()`), so NO
 * prior-identity data (me, board role, repos, sonar status, tickets, members)
 * can leak across the boundary.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});
