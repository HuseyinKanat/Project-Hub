/**
 * Utility to fix authentication token issues
 * Common problem: jarwis-backend token in localStorage instead of Admin token
 */

import { useAuth } from "@/stores/auth";

const ADMIN_TOKEN = "change-me-on-first-login";
const JARWIS_BACKEND_TOKEN_PREFIX = "1c7f53fb";

export function checkAndFixAuthToken(): void {
  const currentToken = localStorage.getItem("projecthub.token");

  if (!currentToken) {
    console.log("[Auth Fix] No token in localStorage, setting Admin token");
    useAuth.getState().setToken(ADMIN_TOKEN);
    localStorage.setItem("projecthub.token", ADMIN_TOKEN);
    return;
  }

  // Check if it's jarwis-backend token (wrong token)
  if (currentToken.startsWith(JARWIS_BACKEND_TOKEN_PREFIX)) {
    console.error("[Auth Fix] Found jarwis-backend token, replacing with Admin token");
    console.error("[Auth Fix] Old token:", currentToken.substring(0, 8) + "...");
    useAuth.getState().setToken(ADMIN_TOKEN);
    localStorage.setItem("projecthub.token", ADMIN_TOKEN);
    console.log("[Auth Fix] Token fixed - now using Admin token");

    // Force page reload to reconnect with correct token
    window.location.reload();
    return;
  }

  // Token exists and looks valid
  console.log("[Auth Fix] Token check passed:", currentToken === ADMIN_TOKEN ? "Admin token" : "Custom token");
}

// Auto-fix on import
if (typeof window !== "undefined") {
  checkAndFixAuthToken();
}