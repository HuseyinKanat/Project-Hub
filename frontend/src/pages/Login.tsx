import { FormEvent, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { verifyToken } from "@/api/client";
import { useAuth } from "@/stores/auth";

// Dev tokens for quick login — sourced from .env.development.local (gitignored).
// Copy frontend/.env.development.example to frontend/.env.development.local and
// fill in your own tokens. Buttons only appear when the variable is defined.
const devTokens: Record<string, string> = Object.fromEntries(
  (
    [
      ["admin",    import.meta.env.VITE_DEV_TOKEN_ADMIN],
      ["pm",       import.meta.env.VITE_DEV_TOKEN_PM],
      ["architect",import.meta.env.VITE_DEV_TOKEN_ARCHITECT],
      ["backend",  import.meta.env.VITE_DEV_TOKEN_BACKEND],
      ["frontend", import.meta.env.VITE_DEV_TOKEN_FRONTEND],
      ["reviewer", import.meta.env.VITE_DEV_TOKEN_REVIEWER],
      ["qa",       import.meta.env.VITE_DEV_TOKEN_QA],
    ] as [string, string | undefined][]
  )
  .filter((entry): entry is [string, string] => {
    const v = entry[1];
    return v !== undefined && v !== "";
  })
);

export function LoginPage() {
  const setToken = useAuth((s) => s.setToken);
  const navigate = useNavigate();
  const [token, setLocalToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoLoginAttempted, setAutoLoginAttempted] = useState(false);

  // Dev mode: Auto-login with Admin token if VITE_DEV_TOKEN_ADMIN is configured.
  useEffect(() => {
    // Prevent multiple executions or running in production
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
      const adminToken = devTokens.admin;
      if (adminToken) {
        // No token, auto-login with Admin token in dev mode (only if configured)
        console.log('[Login] Dev mode: Auto-login with Admin token');
        setLocalToken(adminToken);
        verifyToken(adminToken).then(ok => {
          if (ok) {
            setToken(adminToken);
            navigate("/", { replace: true });
          }
        });
      }
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-base px-6">
      {/* Faint cyan spotlight behind the card (accent wash, ~6%). Decorative. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 50% 38%, var(--accent-subtle), transparent 60%)",
        }}
      />
      <form
        onSubmit={onSubmit}
        className="relative z-10 w-full max-w-sm space-y-4 rounded-lg border p-6"
        style={{
          background: "color-mix(in srgb, var(--bg-surface) 94%, transparent)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          borderColor: "var(--hairline-cyan)",
          boxShadow: "var(--shadow-glass)",
        }}
        aria-label="Login form"
      >
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-text-primary">
            Project<span className="text-accent">Hub</span>
          </h1>
          <p className="text-sm text-text-secondary">
            Bearer token ile giriş yap. (Admin için <code className="mono rounded bg-inset px-1 text-text-secondary">ADMIN_PASSWORD</code>.)
          </p>
        </div>
        <label className="block space-y-1">
          <span className="text-sm font-medium text-text-primary">Bearer token</span>
          <input
            className="input font-mono"
            type="password"
            value={token}
            onChange={(e) => setLocalToken(e.target.value)}
            autoFocus
            required
            autoComplete="off"
            aria-label="Bearer token"
            placeholder="••••••••"
          />
        </label>
        {error && (
          <div
            className="rounded-md px-3 py-2 text-sm text-danger"
            style={{ background: "var(--danger-soft)" }}
            role="alert"
            aria-live="polite"
          >
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

        {/* Quick dev login — only renders when VITE_DEV_TOKEN_* vars are configured */}
        {import.meta.env.DEV && Object.keys(devTokens).length > 0 && (
          <div className="border-t border-hairline pt-4">
            <p className="mb-2 text-xs text-text-muted">Hızlı giriş (dev):</p>
            <div className="flex flex-wrap gap-1">
              {Object.entries(devTokens).map(([role, devToken]) => (
                <button
                  key={role}
                  type="button"
                  onClick={() => {
                    setLocalToken(devToken);
                  }}
                  className="rounded border border-hairline bg-raised px-2 py-1 text-[11px] text-text-secondary hover:bg-accent-soft hover:text-accent"
                >
                  {role}
                </button>
              ))}
            </div>
          </div>
        )}
      </form>
    </div>
  );
}
