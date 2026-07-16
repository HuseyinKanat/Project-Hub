/**
 * Profile.tsx — PH-323 (consumes the PH-322 REST contract).
 *
 * The ONLY write surface for a user's identity + workspace layout:
 *  - owner_slug editor (human-self only; agents show a read-only derived owner)
 *    — client regex-gated (mirrors ^[a-z0-9][a-z0-9-]{0,19}$), block-on-invalid
 *    with NO REST call (E1). A valid save → PUT /api/profile → toast + invalidate
 *    ['profile'] AND ['me'] (owner feeds useBoardRole + the members self row, R4).
 *  - per-board local-path CRUD — rows derive from useMe().memberships so a
 *    path-less board still offers an ADD row (R3). Remove = PUT with local_path=""
 *    (remove-on-empty; no DELETE endpoint). Each mutation invalidates
 *    ['project-paths', boardKey] AND ['members', boardKey] so both views refresh
 *    without a reload. When owner is unresolved the whole path form is LOCKED (E2,
 *    mirrors the backend 422 owner_unresolved).
 *
 * The GET project-paths endpoint is per-board (no list-all route), so each row
 * fans out its own useQuery(['project-paths', boardKey]) — isolated loading/error.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderGit2, Save, Trash2, User } from "lucide-react";

import { api } from "@/api/client";
import { Toast } from "@/components/Toast";
import { useMe } from "@/hooks/useMe";
import {
  canSaveOwnerSlug,
  OWNER_SLUG_MAX,
  validateLocalPath,
  validateOwnerSlug,
} from "@/lib/profile/validation";

type ToastState = { variant: "success" | "error"; message: string };

export function ProfilePage() {
  const qc = useQueryClient();
  const { data: me, isLoading: meLoading } = useMe();
  const [toast, setToast] = useState<ToastState | null>(null);

  const profileQuery = useQuery({
    queryKey: ["profile"],
    queryFn: () => api.getProfile(),
  });
  const profile = profileQuery.data;

  // Seed (and RE-seed after a successful save/invalidation) the editable draft from
  // the persisted column — the effect fires only when owner_slug actually changes,
  // so it never clobbers in-flight typing.
  const [slugDraft, setSlugDraft] = useState("");
  useEffect(() => {
    setSlugDraft(profile?.owner_slug ?? "");
  }, [profile?.owner_slug]);

  const slugMutation = useMutation({
    mutationFn: (slug: string) => api.updateProfile(slug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile"] });
      qc.invalidateQueries({ queryKey: ["me"] }); // R4: owner feeds members self-row + useBoardRole
      setToast({ variant: "success", message: "Owner slug saved." });
    },
    onError: (err: Error) => {
      // A 409 (duplicate) / 422 (regex) message surfaces here verbatim (no crash).
      setToast({
        variant: "error",
        message: err.message || "Failed to save owner slug.",
      });
    },
  });

  const isHuman = profile?.kind === "human";
  // Inline error only once a value is ENTERED — an untouched empty field is not an
  // error, it just leaves Save disabled.
  const slugError = slugDraft.length > 0 ? validateOwnerSlug(slugDraft) : null;
  const canSave = profile
    ? canSaveOwnerSlug(profile.kind, profile.owner_slug, slugDraft)
    : false;
  const ownerResolved = profile?.owner ?? null;

  function handleSlugSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!profile || !canSave) return; // belt 1: disabled-state guard
    if (validateOwnerSlug(slugDraft) !== null) return; // belt 2: E1 — never a PUT on invalid
    slugMutation.mutate(slugDraft);
  }

  const memberships = [...(me?.memberships ?? [])].sort((a, b) =>
    a.board_key.localeCompare(b.board_key),
  );

  const loading = meLoading || profileQuery.isLoading;

  return (
    <section className="mx-auto max-w-3xl space-y-6">
      {toast && (
        <Toast
          variant={toast.variant}
          message={toast.message}
          onDismiss={() => setToast(null)}
          durationMs={3000}
        />
      )}

      <header className="flex items-center gap-2">
        <User className="h-6 w-6 text-accent" aria-hidden />
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
      </header>

      {loading && (
        <output className="block text-sm text-text-muted" aria-live="polite">
          Loading…
        </output>
      )}

      {profileQuery.isError && (
        <div
          className="rounded-md px-3 py-2 text-sm text-danger"
          style={{ background: "var(--danger-soft)" }}
          role="alert"
        >
          {profileQuery.error instanceof Error
            ? profileQuery.error.message
            : "Failed to load profile."}
        </div>
      )}

      {profile && (
        <>
          {/* Identity + owner_slug ------------------------------------------------ */}
          <div className="card space-y-4 p-5">
            <div className="flex items-center justify-between gap-3">
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-medium text-text-primary">
                  {profile.display_name}
                </span>
                <span
                  className={`inline-block w-fit rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                    profile.kind === "agent"
                      ? "bg-accent-soft text-accent"
                      : "bg-success-soft text-success"
                  }`}
                >
                  {profile.kind === "agent" ? "Agent" : "Human"}
                </span>
              </div>
              <div className="text-right">
                <span className="block text-xs text-text-muted">Resolved owner</span>
                <span className="mono text-sm text-text-secondary">
                  {ownerResolved ?? "—"}
                </span>
              </div>
            </div>

            {isHuman ? (
              <form onSubmit={handleSlugSubmit} className="space-y-2">
                <label
                  htmlFor="owner-slug-input"
                  className="block text-sm font-medium text-text-secondary"
                >
                  Owner slug
                </label>
                <div className="flex flex-wrap items-start gap-2">
                  <div className="flex-1 min-w-56">
                    <input
                      id="owner-slug-input"
                      type="text"
                      value={slugDraft}
                      onChange={(e) => setSlugDraft(e.target.value)}
                      className="input w-full"
                      placeholder="e.g. huseyin"
                      maxLength={OWNER_SLUG_MAX}
                      autoCapitalize="none"
                      autoCorrect="off"
                      spellCheck={false}
                      aria-invalid={slugError != null}
                      aria-describedby="owner-slug-help owner-slug-error"
                      data-testid="owner-slug-input"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={!canSave || slugMutation.isPending}
                    className="btn btn-primary inline-flex items-center gap-1.5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="owner-slug-save"
                  >
                    <Save className="h-4 w-4" />
                    {slugMutation.isPending ? "Saving…" : "Save"}
                  </button>
                </div>
                {slugError ? (
                  <p
                    id="owner-slug-error"
                    className="text-xs text-danger"
                    role="alert"
                    data-testid="owner-slug-error"
                  >
                    {slugError}
                  </p>
                ) : (
                  <p id="owner-slug-help" className="text-xs text-text-muted">
                    Lowercase letters, digits and hyphens; up to {OWNER_SLUG_MAX}{" "}
                    characters. Used to key your per-board project paths.
                  </p>
                )}
              </form>
            ) : (
              <div className="space-y-1">
                <span className="block text-sm font-medium text-text-secondary">
                  Owner slug
                </span>
                <p className="text-xs text-text-muted">
                  Agents derive their owner from the display-name suffix — this field
                  is read-only.
                </p>
              </div>
            )}
          </div>

          {/* Per-board project paths -------------------------------------------- */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <FolderGit2 className="h-5 w-5 text-text-muted" aria-hidden />
              <h2 className="text-lg font-semibold tracking-tight">
                Project paths
              </h2>
            </div>
            <p className="text-sm text-text-secondary">
              Your local checkout path for each board you belong to. Stored verbatim
              (machine-local, never validated). Clear a value to remove it.
            </p>

            {!ownerResolved ? (
              <div
                className="flex items-center gap-2 rounded-md bg-warning-soft px-4 py-3 text-sm text-warning"
                role="note"
                data-testid="paths-locked"
              >
                Set your owner slug first to register project paths.
              </div>
            ) : memberships.length === 0 ? (
              <div className="rounded-md border border-dashed border-hairline py-8 text-center text-sm text-text-muted">
                You are not a member of any board yet.
              </div>
            ) : (
              <ul className="space-y-2" data-testid="project-paths-list">
                {memberships.map((m) => (
                  <BoardPathRow
                    key={m.board_key}
                    boardKey={m.board_key}
                    onToast={setToast}
                  />
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  );
}

interface BoardPathRowProps {
  boardKey: string;
  onToast: (t: ToastState) => void;
}

/**
 * One board's local-path editor. Owns its own query (the GET is per-board) + its own
 * upsert/remove mutation, so a slow/failing board never blocks its siblings.
 */
function BoardPathRow({ boardKey, onToast }: Readonly<BoardPathRowProps>) {
  const qc = useQueryClient();

  const pathQuery = useQuery({
    queryKey: ["project-paths", boardKey],
    queryFn: () => api.getProjectPath(boardKey),
  });
  const persisted = pathQuery.data?.local_path ?? "";

  const [draft, setDraft] = useState("");
  useEffect(() => {
    setDraft(persisted);
  }, [persisted]);

  const mutation = useMutation({
    mutationFn: (localPath: string) =>
      api.putProjectPath({ board: boardKey, localPath }),
    onSuccess: (_data, localPath) => {
      onToast({
        variant: "success",
        message:
          localPath === ""
            ? `Path removed for ${boardKey}.`
            : `Path saved for ${boardKey}.`,
      });
    },
    onError: (err: Error) => {
      onToast({
        variant: "error",
        message: err.message || `Failed to save path for ${boardKey}.`,
      });
    },
    onSettled: () => {
      // Both caches carry this path: the profile row + the enriched members roster.
      qc.invalidateQueries({ queryKey: ["project-paths", boardKey] });
      qc.invalidateQueries({ queryKey: ["members", boardKey] });
    },
  });

  const pathError = validateLocalPath(draft);
  const dirty = draft !== persisted;
  const canSavePath = dirty && pathError === null && draft.length > 0;
  const canRemove = persisted.length > 0;
  const inputId = `path-input-${boardKey}`;
  const errorId = `path-error-${boardKey}`;

  function handleSave() {
    if (!canSavePath || mutation.isPending) return;
    mutation.mutate(draft);
  }
  function handleRemove() {
    if (!canRemove || mutation.isPending) return;
    mutation.mutate(""); // remove-on-empty sentinel
  }

  return (
    <li
      className="rounded-md border border-hairline p-3"
      data-testid={`path-row-${boardKey}`}
    >
      <div className="flex flex-wrap items-start gap-2">
        <label
          htmlFor={inputId}
          className="mono mt-2 inline-flex shrink-0 items-center rounded-sm border border-hairline-cyan bg-accent-soft px-2 py-0.5 text-2xs font-semibold text-accent"
        >
          {boardKey}
        </label>
        <div className="min-w-56 flex-1">
          <input
            id={inputId}
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="input w-full"
            placeholder={
              pathQuery.isLoading ? "Loading…" : "/path/to/your/checkout"
            }
            disabled={pathQuery.isLoading}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-invalid={pathError != null}
            aria-describedby={pathError ? errorId : undefined}
            data-testid={`path-input-${boardKey}`}
          />
          {pathError && (
            <p
              id={errorId}
              className="mt-1 text-xs text-danger"
              role="alert"
            >
              {pathError}
            </p>
          )}
          {pathQuery.isError && (
            <p className="mt-1 text-xs text-danger" role="alert">
              {pathQuery.error instanceof Error
                ? pathQuery.error.message
                : "Failed to load path."}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={handleSave}
          disabled={!canSavePath || mutation.isPending}
          className="btn btn-primary inline-flex items-center gap-1.5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          data-testid={`path-save-${boardKey}`}
        >
          <Save className="h-4 w-4" />
          Save
        </button>
        <button
          type="button"
          onClick={handleRemove}
          disabled={!canRemove || mutation.isPending}
          aria-label={`Remove local path for ${boardKey}`}
          className="btn btn-secondary inline-flex items-center gap-1.5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          data-testid={`path-remove-${boardKey}`}
        >
          <Trash2 className="h-4 w-4" />
          Remove
        </button>
      </div>
    </li>
  );
}
