import { FormEvent, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { verifyToken } from "@/api/client";
import { useAuth } from "@/stores/auth";

// Dev tokens for quick login (matches .mcp.json)
const DEV_TOKENS = {
  admin: "change-me-on-first-login",  // Admin token - DEFAULT for dev
  pm: "26977b2fc3dc69082f1b430d2f21a3deb0d4ca56ae6cf2f6",
  architect: "d6a9f91c9721e223474a7c6ce4302c6070e9484833d2b673",
  backend: "1c7f53fbaa73f9cf813df11666a9e7ec098523dc04871335",
  frontend: "cf08819068da8c0684d6037d475191229c18b0af5489d5dd",
  reviewer: "7946bd32eac67e967a7e07eed038204bf485e973f0d25fed",
  qa: "c173183e3111c7e0f30a6fa1ff68cc71d06fdee64e035945",
};

export function LoginPage() {
  const setToken = useAuth((s) => s.setToken);
  const navigate = useNavigate();
  const [token, setLocalToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoLoginAttempted, setAutoLoginAttempted] = useState(false);

  // Dev mode: Auto-login with Admin token
  useEffect(() => {
    // Prevent multiple executions
    if (!import.meta.env.DEV || autoLoginAttempted) {
      return;
    }

    setAutoLoginAttempted(true);

    // Check if already has a token in localStorage
    const existingToken = localStorage.getItem('projecthub.token');
    if (existingToken) {
      // If there's already a token, verify it
      verifyToken(existingToken).then(ok => {
        if (ok) {
          setToken(existingToken);
          navigate("/", { replace: true });
        }
      });
    } else {
      // No token, auto-login with Admin token in dev mode
      console.log('[Login] Dev mode: Auto-login with Admin token');
      setLocalToken(DEV_TOKENS.admin);
      // Auto-submit with Admin token
      verifyToken(DEV_TOKENS.admin).then(ok => {
        if (ok) {
          setToken(DEV_TOKENS.admin);
          navigate("/", { replace: true });
        }
      });
    }
  }, []); // Empty dependencies - run only once on mount

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const ok = await verifyToken(token.trim());
      if (!ok) {
        setError("Token reddedildi. Admin password'ünü kontrol et.");
        return;
      }
      setToken(token.trim());
      navigate("/", { replace: true });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6 dark:bg-slate-900">
      <form
        onSubmit={onSubmit}
        className="card w-full max-w-sm space-y-4 p-6"
        aria-label="Login form"
      >
        <div className="space-y-1">
          <h1 className="text-xl font-semibold dark:text-slate-100">ProjectHub</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Bearer token ile giriş yap. (Admin için <code className="rounded bg-slate-100 px-1 dark:bg-slate-700 dark:text-slate-300">ADMIN_PASSWORD</code>.)
          </p>
        </div>
        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Bearer token</span>
          <input
            className="input font-mono"
            type="password"
            value={token}
            onChange={(e) => setLocalToken(e.target.value)}
            autoFocus
            required
            placeholder="••••••••"
          />
        </label>
        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400" role="alert">
            {error}
          </div>
        )}
        <button
          type="submit"
          className="btn-primary w-full"
          disabled={submitting || token.trim().length === 0}
        >
          {submitting ? "Doğrulanıyor…" : "Giriş yap"}
        </button>

        {/* Quick dev login */}
        <div className="border-t border-slate-200 pt-4 dark:border-slate-700">
          <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">Hızlı giriş (dev):</p>
          <div className="flex flex-wrap gap-1">
            {Object.entries(DEV_TOKENS).map(([role, devToken]) => (
              <button
                key={role}
                type="button"
                onClick={() => {
                  setLocalToken(devToken);
                }}
                className="rounded bg-slate-100 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600"
              >
                {role}
              </button>
            ))}
          </div>
        </div>
      </form>
    </div>
  );
}
