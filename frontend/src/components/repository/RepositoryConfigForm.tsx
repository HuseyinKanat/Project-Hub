/**
 * RepositoryConfigForm — G13 (PH-162)
 *
 * Admin-only config form to connect or update a board's git repository.
 * - Provider select (github | gitlab | local)
 * - remote_url (required for github/gitlab, optional for local)
 * - default_branch (default "main")
 * - local_path (required, /repos/ prefix enforced client-side + 422 inline)
 *
 * Mutation: PUT /api/boards/{boardKey}/repository → onSuccess invalidates git status.
 *
 * A11y:
 *  - All inputs have associated <label>
 *  - Errors linked with aria-describedby
 *  - Loading state disables submit button
 */

import { useState, FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiRequestError } from "@/api/client";
import type { GitProvider, RepositoryResponse } from "@/types/git";

interface RepositoryConfigFormProps {
  boardKey: string;
  /** Initial values from existing repo row; undefined when not yet connected */
  initialValues?: {
    provider?: string;
    remote_url?: string | null;
    default_branch?: string;
    local_path?: string;
  };
  /** Called after successful save so parent can update status */
  onSuccess?: (repo: RepositoryResponse) => void;
}

const PROVIDERS: { value: GitProvider; label: string }[] = [
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
  { value: "local", label: "Local" },
];

export function RepositoryConfigForm({
  boardKey,
  initialValues,
  onSuccess,
}: RepositoryConfigFormProps) {
  const qc = useQueryClient();

  const [provider, setProvider] = useState<GitProvider>(
    (initialValues?.provider as GitProvider | undefined) ?? "local",
  );
  const [remoteUrl, setRemoteUrl] = useState(initialValues?.remote_url ?? "");
  const [defaultBranch, setDefaultBranch] = useState(
    initialValues?.default_branch ?? "main",
  );
  const [localPath, setLocalPath] = useState(initialValues?.local_path ?? "/repos/");

  // field-level 422 errors
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [globalError, setGlobalError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.setRepository(boardKey, {
        provider,
        remote_url: remoteUrl || null,
        default_branch: defaultBranch || "main",
        local_path: localPath,
      }),
    onSuccess: (repo) => {
      setFieldErrors({});
      setGlobalError(null);
      qc.invalidateQueries({ queryKey: ["git", boardKey, "status"] });
      onSuccess?.(repo);
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 422) {
        // body.detail is Pydantic ValidationError list or plain string
        const detail = err.body?.detail;
        if (Array.isArray(detail)) {
          const errors: Record<string, string> = {};
          for (const d of detail as { loc: string[]; msg: string }[]) {
            const field = d.loc[d.loc.length - 1] ?? "global";
            errors[field] = d.msg;
          }
          setFieldErrors(errors);
          setGlobalError(null);
        } else {
          setFieldErrors({});
          setGlobalError(String(detail ?? err.message));
        }
      } else {
        setFieldErrors({});
        setGlobalError(err instanceof Error ? err.message : "Kayıt başarısız.");
      }
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setFieldErrors({});
    setGlobalError(null);

    // client-side prefix check (mirrors backend validator — fast feedback)
    if (!localPath.startsWith("/repos/")) {
      setFieldErrors({ local_path: "local_path /repos/ ile başlamalı" });
      return;
    }

    mutation.mutate();
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-4"
      aria-label="Repository yapılandırma formu"
    >
      {globalError && (
        <div
          className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400"
          role="alert"
        >
          {globalError}
        </div>
      )}

      {/* Provider */}
      <div className="space-y-1">
        <label
          htmlFor="repo-provider"
          className="block text-sm font-medium text-slate-700 dark:text-slate-300"
        >
          Provider
        </label>
        <select
          id="repo-provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value as GitProvider)}
          className="input"
          disabled={mutation.isPending}
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      {/* Remote URL */}
      {provider !== "local" && (
        <div className="space-y-1">
          <label
            htmlFor="repo-remote-url"
            className="block text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            Remote URL{" "}
            <span className="text-red-500" aria-hidden="true">
              *
            </span>
          </label>
          <input
            id="repo-remote-url"
            type="url"
            value={remoteUrl}
            onChange={(e) => setRemoteUrl(e.target.value)}
            placeholder="https://github.com/org/repo.git"
            className={`input ${fieldErrors.remote_url ? "border-red-500 dark:border-red-400" : ""}`}
            aria-describedby={fieldErrors.remote_url ? "remote-url-error" : undefined}
            disabled={mutation.isPending}
            required
          />
          {fieldErrors.remote_url && (
            <p id="remote-url-error" className="text-xs text-red-600 dark:text-red-400" role="alert">
              {fieldErrors.remote_url}
            </p>
          )}
        </div>
      )}

      {/* Default branch */}
      <div className="space-y-1">
        <label
          htmlFor="repo-default-branch"
          className="block text-sm font-medium text-slate-700 dark:text-slate-300"
        >
          Default branch
        </label>
        <input
          id="repo-default-branch"
          type="text"
          value={defaultBranch}
          onChange={(e) => setDefaultBranch(e.target.value)}
          placeholder="main"
          className="input"
          disabled={mutation.isPending}
        />
      </div>

      {/* Local path */}
      <div className="space-y-1">
        <label
          htmlFor="repo-local-path"
          className="block text-sm font-medium text-slate-700 dark:text-slate-300"
        >
          Local path{" "}
          <span className="text-red-500" aria-hidden="true">
            *
          </span>
        </label>
        <input
          id="repo-local-path"
          type="text"
          value={localPath}
          onChange={(e) => setLocalPath(e.target.value)}
          placeholder="/repos/my-project"
          className={`input font-mono text-sm ${
            fieldErrors.local_path ? "border-red-500 dark:border-red-400" : ""
          }`}
          aria-describedby={
            fieldErrors.local_path ? "local-path-error" : "local-path-hint"
          }
          disabled={mutation.isPending}
          required
        />
        {fieldErrors.local_path ? (
          <p id="local-path-error" className="text-xs text-red-600 dark:text-red-400" role="alert">
            {fieldErrors.local_path}
          </p>
        ) : (
          <p id="local-path-hint" className="text-xs text-slate-500 dark:text-slate-400">
            /repos/ ile başlamalı (örn. /repos/project-hub)
          </p>
        )}
      </div>

      <div className="flex justify-end pt-2">
        <button
          type="submit"
          className="btn-primary text-sm"
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
        >
          {mutation.isPending ? "Kaydediliyor..." : "Kaydet"}
        </button>
      </div>
    </form>
  );
}
