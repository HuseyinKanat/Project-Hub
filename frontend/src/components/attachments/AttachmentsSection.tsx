import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Paperclip } from "lucide-react";

import { api } from "@/api/client";

import { AttachmentItem } from "./AttachmentItem";
import { AttachmentUpload } from "./AttachmentUpload";
import {
  beforeAfterAnchors,
  groupAttachmentsByPhase,
  groupAttachmentsByRun,
  hasAnyPhase,
  isSpecKind,
  summarizeIterations,
} from "./grouping";

const UNGROUPED_KEY = "__ungrouped__";

/**
 * AttachmentsSection — PH-297 / PH-312
 *
 * The "Kanıtlar" (Evidence) card — a SIBLING of the Activity card (not a tab).
 *
 * When any attachment carries a `phase` slug (PH-311), the card renders the fix as
 * a CHRONOLOGY (PH-312): an iteration-summary line, an optional before/after
 * comparison, then phase groups in deterministic story order (Reproduce →
 * iterations → resolution). With NO phase anywhere it falls back VERBATIM to the
 * PH-297 run_id grouping (newest run on top, "Diğer" bucket last). Either way,
 * groups are collapsible <details> that collapse by default past 3 of them.
 * Loading / empty / error(+retry) states are explicit. The optional upload control
 * is permission-aware (`canUpload`) with the server's 403 still surfaced inline.
 */
export function AttachmentsSection({
  ticketKey,
  canUpload,
}: Readonly<{ ticketKey: string; canUpload: boolean }>) {
  const attachmentsQuery = useQuery({
    queryKey: ["ticket-attachments", ticketKey],
    queryFn: () => api.listAttachments(ticketKey),
    enabled: Boolean(ticketKey),
  });

  // PH-310: spec docs (kind=usecase|testcase) are surfaced as chips under their
  // owning field (AC / Test Plan) on the detail page — keep them OUT of the
  // "Kanıtlar" evidence card so the two surfaces don't double-list the same file.
  const items = (attachmentsQuery.data ?? []).filter((a) => !isSpecKind(a.kind));

  // PH-312: when any evidence carries a phase, tell the fix as a chronology;
  // otherwise fall back to the PH-297 run_id view unchanged. Both strategies are
  // normalised to a common {key,label,items} shape for the shared <details>
  // renderer (`mono` keeps run_id headings monospaced; phase headings are prose).
  const story = hasAnyPhase(items);
  const phaseGroups = story ? groupAttachmentsByPhase(items) : null;
  const groups = phaseGroups
    ? phaseGroups.map((g) => ({
        key: g.slug ?? UNGROUPED_KEY,
        label: g.label,
        items: g.items,
        mono: false,
      }))
    : groupAttachmentsByRun(items).map((g) => ({
        key: g.runId ?? UNGROUPED_KEY,
        label: g.label,
        items: g.items,
        mono: true,
      }));
  const summary = story ? summarizeIterations(items) : null;
  const anchors = phaseGroups ? beforeAfterAnchors(phaseGroups) : null;

  const defaultOpen = groups.length <= 3;
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});
  const isOpen = (key: string) => openMap[key] ?? defaultOpen;

  let body: React.ReactNode;
  if (attachmentsQuery.isLoading) {
    body = (
      <div className="h-16 animate-pulse rounded-md bg-raised" aria-hidden="true" />
    );
  } else if (attachmentsQuery.isError) {
    body = (
      <div className="flex flex-col items-start gap-2" role="alert">
        <p className="text-xs text-danger">Kanıtlar yüklenemedi.</p>
        <button
          type="button"
          onClick={() => attachmentsQuery.refetch()}
          className="btn-ghost btn-sm"
        >
          Tekrar dene
        </button>
      </div>
    );
  } else if (items.length === 0) {
    body = <p className="text-xs text-text-muted">Henüz kanıt yok.</p>;
  } else {
    body = (
      <div className="flex flex-col gap-2.5">
        {summary && (
          <p className="text-xs font-medium text-text-secondary">{summary}</p>
        )}

        {anchors?.before && anchors.after && (
          <div className="rounded-md border border-hairline p-2.5">
            <p className="mb-2 text-xs font-medium text-text-secondary">
              Öncesi / Sonrası
            </p>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] text-text-muted">
                  {anchors.before.label}
                </span>
                <ul className="flex flex-col gap-2">
                  {anchors.before.items.map((a) => (
                    <AttachmentItem
                      key={`ba-before-${a.id}`}
                      ticketKey={ticketKey}
                      attachment={a}
                    />
                  ))}
                </ul>
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] text-text-muted">
                  {anchors.after.label}
                </span>
                <ul className="flex flex-col gap-2">
                  {anchors.after.items.map((a) => (
                    <AttachmentItem
                      key={`ba-after-${a.id}`}
                      ticketKey={ticketKey}
                      attachment={a}
                    />
                  ))}
                </ul>
              </div>
            </div>
          </div>
        )}

        {groups.map((g) => (
          <details
            key={g.key}
            open={isOpen(g.key)}
            onToggle={(e) =>
              setOpenMap((m) => ({
                ...m,
                [g.key]: (e.target as HTMLDetailsElement).open,
              }))
            }
            className="rounded-md border border-hairline"
          >
            <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-2 text-xs text-text-secondary">
              <span className={`${g.mono ? "mono " : ""}truncate`}>{g.label}</span>
              <span className="tab-count">{g.items.length}</span>
            </summary>
            <ul className="flex flex-col gap-2 p-2.5 pt-0">
              {g.items.map((a) => (
                <AttachmentItem key={a.id} ticketKey={ticketKey} attachment={a} />
              ))}
            </ul>
          </details>
        ))}
      </div>
    );
  }

  return (
    <section
      className="card"
      style={{ padding: 0 }}
      aria-labelledby="attachments-heading"
    >
      <div className="field-head" style={{ padding: "12px 16px" }}>
        <h2
          id="attachments-heading"
          className="field-title flex items-center gap-2"
        >
          <Paperclip className="h-4 w-4 text-text-muted" aria-hidden="true" />
          Kanıtlar
          <span className="tab-count">{items.length}</span>
        </h2>
      </div>

      <div
        className="field-body"
        style={{ display: "flex", flexDirection: "column", gap: 14 }}
      >
        {canUpload && <AttachmentUpload ticketKey={ticketKey} />}
        {body}
      </div>
    </section>
  );
}
