/**
 * OverviewSummary.tsx — PH-339: the query-owning root of the board "Genel Bakış"
 * project-summary block, mounted below the (PH-335) epic-progress panel inside
 * the (PH-337) OverviewTab. Owns its OWN `["board", boardKey, "summary"]` query
 * (api.getBoardSummary) — SELF-CONTAINED so a summary-endpoint failure degrades
 * INLINE and NON-BLOCKING (UC E1) while the epic-progress panel (a separate
 * query/component) stays fully usable (AC4). Because the overview tab is
 * conditionally mounted, the query is tab-lazy (same pattern as EpicProgressPanel
 * / NotesPanel).
 *
 * States (AC4): loading skeleton → inline error+Retry → empty-state (200+null:
 * "henüz özet yok"; a write-authorised user gets a Create CTA, others just the
 * message) → data (the Turkish sections + the milestone timeline).
 *
 * View (AC1): purpose / status / progress / highlights render as SEPARATE headed
 * regions, each drawn with MarkdownRenderer (a human's `- ` bullets show as a
 * list; content round-trips verbatim — the sections are opaque free-text on the
 * backend). An empty section is simply not drawn.
 *
 * Edit (AC3): the "Düzenle" toggle (write-gated: pm/orchestrator/admin, HIDE-
 * when-false — canCreateBoard/RepositoryList pattern) swaps in the SummaryEditor,
 * which full-replace-upserts and, on success, invalidates this query + returns to
 * the view. A non-writer never sees the toggle (read-only); the PUT 403 is only
 * the submit backstop.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, FileText, Loader2, Pencil, Plus } from "lucide-react";

import { api, ApiRequestError } from "@/api/client";
import { useBoardRole } from "@/hooks/useMe";
import { humaniseRelativeTr } from "@/lib/time";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { MilestoneTimeline } from "./MilestoneTimeline";
import { SummaryEditor } from "./SummaryEditor";
import type { BoardSummary } from "@/types/api";

/** Roles granted the `board.summary.write` capability (permissions.py). */
const WRITE_ROLES = ["pm", "orchestrator", "admin"];

const VIEW_SECTIONS: ReadonlyArray<{ key: keyof BoardSummary; label: string }> = [
  { key: "purpose", label: "Amaç" },
  { key: "status", label: "Genel Durum" },
  { key: "progress", label: "İlerleme" },
  { key: "highlights", label: "Öne Çıkan Kapatılanlar" },
];

function getErrorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    if (err.status === 403) return "Bu board'un özetini görüntüleme yetkiniz yok.";
    if (err.status === 404) return "Board bulunamadı.";
    return err.message || "Özet yüklenemedi.";
  }
  if (err instanceof Error) return err.message || "Özet yüklenemedi.";
  return "Özet yüklenemedi.";
}

export function OverviewSummary({
  boardKey,
}: Readonly<{ boardKey: string }>) {
  const role = useBoardRole(boardKey);
  const canEdit = WRITE_ROLES.includes(role ?? "");
  const [editing, setEditing] = useState(false);

  const query = useQuery({
    queryKey: ["board", boardKey, "summary"],
    queryFn: () => api.getBoardSummary(boardKey),
    enabled: Boolean(boardKey),
  });

  // Loading — a compact busy card; the epic-progress panel above is untouched.
  if (query.isLoading) {
    return (
      <section
        className="card flex flex-col gap-2 px-4 py-3"
        aria-busy="true"
        aria-label="Proje özeti yükleniyor"
        data-testid="overview-summary-loading"
      >
        <span className="eyebrow text-text-muted">Proje Özeti</span>
        <div className="h-2 w-2/3 animate-pulse rounded-pill bg-inset" />
        <div className="h-2 w-1/2 animate-pulse rounded-pill bg-inset" />
      </section>
    );
  }

  // Error — INLINE + NON-BLOCKING (UC E1): the epic-progress panel (a separate
  // query) is unaffected; a Retry re-runs just this query.
  if (query.isError) {
    return (
      <output
        className="card flex flex-wrap items-center gap-2 border-dashed border-hairline px-4 py-2.5 text-sm text-text-muted"
        aria-label="Proje özeti yüklenemedi"
        data-testid="overview-summary-error"
      >
        <span className="eyebrow text-text-muted">Proje Özeti</span>
        <span className="text-warning">{getErrorMessage(query.error)}</span>
        <button
          type="button"
          onClick={() => void query.refetch()}
          className="ml-auto text-2xs font-medium text-accent hover:text-accent-hover"
        >
          Yeniden dene
        </button>
      </output>
    );
  }

  const data: BoardSummary | null = query.data ?? null;

  // Edit mode (write-gated). `data` seeds the form (null → a fresh empty form).
  if (editing && canEdit) {
    return (
      <section
        className="card space-y-4 px-4 py-4"
        aria-labelledby="overview-summary-heading"
        data-testid="overview-summary"
      >
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-accent" aria-hidden="true" />
          <h2
            id="overview-summary-heading"
            className="text-base font-semibold text-text-primary"
          >
            Proje Özeti — Düzenle
          </h2>
        </div>
        <SummaryEditor
          boardKey={boardKey}
          initial={data}
          onCancel={() => setEditing(false)}
          onSaved={() => setEditing(false)}
        />
      </section>
    );
  }

  // View mode.
  return (
    <section
      className="card space-y-4 px-4 py-4"
      aria-labelledby="overview-summary-heading"
      data-testid="overview-summary"
    >
      <div className="flex items-center gap-2">
        <FileText className="h-5 w-5 text-accent" aria-hidden="true" />
        <h2
          id="overview-summary-heading"
          className="text-base font-semibold text-text-primary"
        >
          Proje Özeti
        </h2>
        {data && canEdit && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="btn-ghost ml-auto inline-flex items-center gap-1.5 text-sm"
            data-testid="summary-edit-button"
          >
            <Pencil className="h-4 w-4" aria-hidden="true" />
            Düzenle
          </button>
        )}
      </div>

      {data === null ? (
        // Empty-state (200 + null) — distinct from the unknown-board 404.
        <div
          className="flex flex-col items-start gap-3 rounded-md border border-dashed border-hairline px-4 py-6 text-sm text-text-muted"
          data-testid="overview-summary-empty"
        >
          <span>Bu board için henüz bir özet oluşturulmadı.</span>
          {canEdit ? (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="btn-primary inline-flex items-center gap-2 text-sm"
              data-testid="summary-create-button"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Özet oluştur
            </button>
          ) : (
            <span className="text-xs">
              Yazma yetkisi olan biri (pm / orchestrator / admin) ekleyene kadar
              burası boş kalır.
            </span>
          )}
        </div>
      ) : (
        <div className="space-y-5">
          {/* Turkish free-text sections — only non-empty ones are drawn (AC1). */}
          {VIEW_SECTIONS.map(({ key, label }) => {
            const value = data[key];
            if (typeof value !== "string" || value.trim().length === 0) {
              return null;
            }
            const headingId = `overview-section-${String(key)}`;
            return (
              <section
                key={String(key)}
                aria-labelledby={headingId}
                className="space-y-1.5"
                data-testid={`summary-section-view-${String(key)}`}
              >
                <h3 id={headingId} className="eyebrow text-text-muted">
                  {label}
                </h3>
                <MarkdownRenderer content={value} />
              </section>
            );
          })}

          {/* Visual milestone timeline (AC2). */}
          <MilestoneTimeline milestones={data.milestones} />

          {/* Provenance footer — last writer + relative time (bilinmiyor/hiç). */}
          <p className="border-t border-hairline pt-2 text-xs text-text-muted">
            Son güncelleyen:{" "}
            <span className="text-text-secondary">
              {data.updated_by_name ?? "bilinmiyor"}
            </span>{" "}
            · {humaniseRelativeTr(data.updated_at)}
          </p>
        </div>
      )}
    </section>
  );
}
