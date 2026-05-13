import { create } from "zustand";

const TOKEN_KEY = "projecthub.token";

interface AuthState {
  token: string | null;
  setToken: (token: string | null) => void;
  logout: () => void;
}

const initialToken =
  typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;

export const useAuth = create<AuthState>((set) => ({
  token: initialToken,
  setToken: (token) => {
    if (typeof window !== "undefined") {
      if (token) window.localStorage.setItem(TOKEN_KEY, token);
      else window.localStorage.removeItem(TOKEN_KEY);
    }
    set({ token });
  },
  logout: () => {
    if (typeof window !== "undefined") window.localStorage.removeItem(TOKEN_KEY);
    set({ token: null });
  },
}));

export const getStoredToken = (): string | null =>
  typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;
