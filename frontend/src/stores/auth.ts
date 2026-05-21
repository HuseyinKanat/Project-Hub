import { create } from "zustand";

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
    set({ token });
  },
  logout: () => {
    if (import.meta.env.DEV) {
      console.log('[Auth] Logout - clearing token from store and localStorage');
    }

    if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY);
    set({ token: null });
  },
}));

export const getStoredToken = (): string | null =>
  typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;
