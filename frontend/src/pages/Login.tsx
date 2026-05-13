import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { verifyToken } from "@/api/client";
import { useAuth } from "@/stores/auth";

export function LoginPage() {
  const setToken = useAuth((s) => s.setToken);
  const navigate = useNavigate();
  const [token, setLocalToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
      <form
        onSubmit={onSubmit}
        className="card w-full max-w-sm space-y-4 p-6"
        aria-label="Login form"
      >
        <div className="space-y-1">
          <h1 className="text-xl font-semibold">ProjectHub</h1>
          <p className="text-sm text-slate-500">
            Bearer token ile giriş yap. (Admin için <code>ADMIN_PASSWORD</code>.)
          </p>
        </div>
        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700">Bearer token</span>
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
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
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
      </form>
    </div>
  );
}
