import { create } from "zustand";

import { queryClient } from "@/lib/queryClient";

import { shouldClearCacheOnIdentityChange } from "./identityGuard";

const TOKEN_KEY = "projecthub.token";

interface AuthState {
  token: string | null;
  setToken: (token: string | null) => void;
  logout: () => void;
}

const initialToken =
  typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;

export const useAuth = create<AuthState>((set, get) => ({
  token: initialToken,
  setToken: (token) => {
    const currentToken = get().token;

    // Development logging for token source tracking
    if (import.meta.env.DEV) {
      console.log('[Auth] Token update:', {
        from: currentToken ? 'existing' : 'null',
        to: token ? 'new token' : 'null',
        source: 'auth store',
        localStorage: typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null
      });
    }

    if (typeof window !== "undefined") {
      if (token) {
        window.localStorage.setItem(TOKEN_KEY, token);
      } else {
        window.localStorage.removeItem(TOKEN_KEY);
      }

      // Validation: ensure localStorage and store stay synchronized
      const storedToken = window.localStorage.getItem(TOKEN_KEY);
      if (storedToken !== token && import.meta.env.DEV) {
        console.warn('[Auth] Token synchronization issue detected:', {
          storeToken: token,
          localStorageToken: storedToken
        });
      }
    }
    // Set the new token BEFORE clearing so the new ["me", token] key is live
    // when observers re-subscribe and refetch (avoids a one-frame fetch under
    // the OLD key).
    set({ token });
    // PH-232: an identity change is a trust boundary — drop ALL prior-identity
    // cache (me, board role, repos, sonar status, tickets, members) so the new
    // identity's gating (isAdmin) is computed without a hard reload and no
    // prior-identity bytes leak. Guard on the actual value change so a no-op
    // re-set (auto-login re-setting the SAME token / component re-mount) does
    // NOT clear → no refetch loop, no flicker.
    if (shouldClearCacheOnIdentityChange(currentToken, token)) queryClient.clear();
  },
  logout: () => {
    const had = get().token;

    if (import.meta.env.DEV) {
      console.log('[Auth] Logout - clearing token from store and localStorage');
    }

    if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY);
    set({ token: null });
    // PH-232: wipe all caches on logout (covers the manual logout AND the two
    // outside-React 401 auto-logouts in client.ts which already route through
    // logout()). Same guard as setToken: only a real change (had → null) clears,
    // so a redundant logout while already logged out is a no-op.
    if (shouldClearCacheOnIdentityChange(had, null)) queryClient.clear();
  },
}));

export const getStoredToken = (): string | null =>
  typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;
