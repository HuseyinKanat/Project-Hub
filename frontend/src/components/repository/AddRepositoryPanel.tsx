/**
 * AddRepositoryPanel — C5 (PH-225)
 *
 * Admin-only "Repository ekle" disclosure with two sub-tabs:
 *
 *  (a) Algılanan (Detected): lists GET /repositories/detect candidates. Each
 *      candidate row shows name / local_path / remote_url / default_branch and a
 *      one-click "Ekle" button that maps the DetectedRepo 1:1 onto a
 *      RepositoryCreatePayload and POSTs it (no manual typing). A candidate whose
 *      `already_linked === true` is greyed + button disabled with a 'Zaten ekli'
 *      marker. Empty detect → graceful 'algılanan git deposu yok' note (NOT an
 *      error), the Manual tab still works.
 *
 *  (b) Elle (Manual): the existing RepositoryConfigForm in `mode='add'` so a
 *      manual add POSTs a NEW repo (does NOT overwrite the primary — the singular
 *      PUT path is reserved for editing the primary).
 *
 * Data:
 *  - The detect query is owned by the PARENT (BoardSettings) and passed in, fired
 *    lazily (only when this panel is open) to respect the 5s FS-scan budget.
 *  - The one-click add mutation invalidates list + detect + git status (refetch,
 *    not optimistic) so the new repo appears and the candidate flips to linked.
 *  - 403 (non-admin) on add → inline 'admin yetkisi gerekli', no crash (PH-224).
 *
 * A11y: tablist/tab/tabpanel roles, candidate rows are <li> in a <ul role=list>,
 * Add buttons carry aria-labels, disabled candidates announce 'Zaten ekli'.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, FolderGit2, Check, Loader2 } from "lucide-react";
import { api, ApiRequestError } from "@/api/client";
import type {
  DetectedRepo,
  RepositoryCreatePayload,
} from "@/types/git";
import { RepositoryConfigForm } from "./RepositoryConfigForm";

type AddTab = "detected" | "manual";

/** Map a detected candidate 1:1 onto the add payload (no manual typing). */
function detectedToPayload(d: DetectedRepo): RepositoryCreatePayload {
  return {
    provider: d.provider_guess,
    remote_url: d.remote_url,
    default_branch: d.default_branch ?? "main",
    local_path: d.local_path,
    name: d.name,
  };
}

// ---------------------------------------------------------------------------
// Detected candidate row
// ---------------------------------------------------------------------------

interface DetectedRowProps {
  candidate: DetectedRepo;
  onAdd: (d: DetectedRepo) => void;
  isAdding: boolean;
}

function DetectedRow({ candidate, onAdd, isAdding }: Readonly<DetectedRowProps>) {
  const linked = candidate.already_linked;
  return (
    <li
      className={`flex flex-col gap-2 rounded-md border border-hairline p-3 sm:flex-row sm:items-center sm:justify-between ${
        linked ? "opacity-50" : ""
      }`}
      data-testid={`detect-row-${candidate.name}`}
      data-linked={linked}
    >
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-text-primary">{candidate.name}</span>
          <span className="badge inline-flex items-center bg-inset px-2 py-0.5 text-xs text-text-secondary">
            {candidate.provider_guess}
          </span>
          {linked && (
            <span
              className="badge inline-flex items-center bg-warning-soft px-2 py-0.5 text-xs text-warning"
              data-testid={`already-linked-${candidate.name}`}
            >
              Zaten ekli
            </span>
          )}
        </div>
        <p className="mono text-xs text-text-muted">{candidate.local_path}</p>
        <div className="flex flex-wrap items-center gap-x-3 text-xs text-text-secondary">
          <span className="max-w-xs truncate">
            {candidate.remote_url ?? (
              <span className="italic text-text-muted">local-only</span>
            )}
          </span>
          {candidate.default_branch && (
            <code className="mono rounded bg-inset px-1.5 py-0.5">
              {candidate.default_branch}
            </code>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={() => onAdd(candidate)}
        disabled={linked || isAdding}
        className="btn-primary inline-flex shrink-0 items-center gap-1.5 py-1.5 text-xs disabled:opacity-50"
        aria-label={
          linked
            ? `${candidate.name} zaten ekli`
            : `${candidate.name} deposunu ekle`
        }
        data-testid={`detect-add-${candidate.name}`}
      >
        {linked ? (
          <>
            <Check className="h-3 w-3" />
            Ekli
          </>
        ) : (
          <>
            {isAdding ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Plus className="h-3 w-3" />
            )}
            Ekle
          </>
        )}
      </button>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

interface AddRepositoryPanelProps {
  boardKey: string;
  detected: DetectedRepo[];
  detectLoading: boolean;
  detectError: boolean;
  onRefetchDetect: () => void;
}

export function AddRepositoryPanel({
  boardKey,
  detected,
  detectLoading,
  detectError,
  onRefetchDetect,
}: Readonly<AddRepositoryPanelProps>) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<AddTab>("detected");
  const [addError, setAddError] = useState<string | null>(null);
  const [addingPath, setAddingPath] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: (candidate: DetectedRepo) =>
      api.git.addRepository(boardKey, detectedToPayload(candidate)),
    onMutate: (candidate) => {
      setAddError(null);
      setAddingPath(candidate.local_path);
    },
    onSuccess: () => {
      // refetch (not optimistic): the candidate flips to already_linked + the
      // new repo appears as a list row.
      qc.invalidateQueries({ queryKey: ["repositories", boardKey] });
      qc.invalidateQueries({ queryKey: ["repositories", boardKey, "detect"] });
      qc.invalidateQueries({ queryKey: ["git", boardKey, "status"] });
    },
    onError: (err) => {
      if (err instanceof ApiRequestError && err.status === 403) {
        setAddError("Bu işlem için admin yetkisi gerekli.");
      } else {
        setAddError(err instanceof Error ? err.message : "Depo eklenemedi.");
      }
    },
    onSettled: () => {
      setAddingPath(null);
    },
  });

  return (
    <div className="space-y-4" data-testid="add-repo-panel">
      {/* Sub-tabs */}
      <div
        className="flex gap-1 border-b border-hairline"
        role="tablist"
        aria-label="Depo ekleme yöntemi"
      >
        {(["detected", "manual"] as AddTab[]).map((t) => {
          const isActive = tab === t;
          const label = t === "detected" ? "Algılanan" : "Elle";
          return (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={`add-${t}-panel`}
              id={`add-${t}-tab`}
              onClick={() => setTab(t)}
              className={`relative inline-flex items-center gap-2 px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "text-accent"
                  : "text-text-secondary hover:text-text-primary"
              }`}
              data-testid={`add-tab-${t}`}
            >
              {t === "detected" ? (
                <FolderGit2 className="h-4 w-4" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              {label}
              {isActive && (
                <span className="pointer-events-none absolute inset-x-2 -bottom-px h-0.5 rounded bg-accent" />
              )}
            </button>
          );
        })}
      </div>

      {/* Detected tab */}
      {tab === "detected" && (
        <div
          id="add-detected-panel"
          role="tabpanel"
          aria-labelledby="add-detected-tab"
          className="space-y-3"
        >
          <div className="flex items-center justify-between">
            <p className="text-xs text-text-muted">
              Bu panonun proje yolu altında algılanan git depoları — eklemek için
              tıklayın.
            </p>
            <button
              type="button"
              onClick={onRefetchDetect}
              disabled={detectLoading}
              className="btn-ghost inline-flex items-center gap-1.5 py-1 text-xs"
              aria-label="Algılananları yenile"
              data-testid="detect-refresh"
            >
              <RefreshCw
                className={`h-3 w-3 ${detectLoading ? "animate-spin" : ""}`}
              />
              Yenile
            </button>
          </div>

          {addError && (
            <div
              className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger"
              role="alert"
              data-testid="detect-add-error"
            >
              {addError}
            </div>
          )}

          {detectLoading && (
            <div className="flex items-center gap-2 py-4 text-sm text-text-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Algılanıyor...</span>
            </div>
          )}

          {!detectLoading && detectError && (
            <output
              className="block rounded-md bg-inset px-4 py-4 text-center text-sm text-text-muted"
              data-testid="detect-error-state"
            >
              Algılama yapılamadı — elle ekleme sekmesini kullanın.
            </output>
          )}

          {!detectLoading && !detectError && detected.length === 0 && (
            <output
              className="block rounded-md bg-inset px-4 py-6 text-center text-sm text-text-muted"
              data-testid="detect-empty-state"
            >
              Bu panonun proje yolu altında algılanan git deposu yok — aşağıdaki
              "Elle" sekmesinden ekleyin.
            </output>
          )}

          {!detectLoading && !detectError && detected.length > 0 && (
            <ul role="list" className="space-y-2" data-testid="detect-list">
              {detected.map((candidate) => (
                <DetectedRow
                  key={candidate.local_path}
                  candidate={candidate}
                  isAdding={addingPath === candidate.local_path}
                  onAdd={(c) => addMutation.mutate(c)}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Manual tab */}
      {tab === "manual" && (
        <div
          id="add-manual-panel"
          role="tabpanel"
          aria-labelledby="add-manual-tab"
          className="pt-1"
        >
          <p className="mb-3 text-xs text-text-muted">
            Algılanmayan bir depoyu elle ekleyin. Bu, birincil depoyu
            değiştirmez — yeni bir depo oluşturur.
          </p>
          <RepositoryConfigForm
            boardKey={boardKey}
            mode="add"
            submitLabel="Ekle"
          />
        </div>
      )}
    </div>
  );
}
