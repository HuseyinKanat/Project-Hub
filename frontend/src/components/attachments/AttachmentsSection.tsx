import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Paperclip } from "lucide-react";

import { api } from "@/api/client";

import { AttachmentItem } from "./AttachmentItem";
import { AttachmentUpload } from "./AttachmentUpload";
import { groupAttachmentsByRun, isSpecKind } from "./grouping";

const UNGROUPED_KEY = "__ungrouped__";

/**
 * AttachmentsSection — PH-297
 *
 * The "Kanıtlar" (Evidence) card — a SIBLING of the Activity card (not a tab).
 * Lists a ticket's attachments grouped by run_id (newest run on top, "Diğer"
 * bucket last) inside collapsible <details>. Groups collapse by default once
 * there are >3 of them. Loading / empty / error(+retry) states are explicit.
 * The optional upload control is permission-aware (`canUpload`, computed by the
 * parent from the board role's `attachment.add` cap) with the server's 403 still
 * surfaced inline as a fallback.
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
  const groups = groupAttachmentsByRun(items);
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
        {groups.map((g) => {
          const gk = g.runId ?? UNGROUPED_KEY;
          return (
            <details
              key={gk}
              open={isOpen(gk)}
              onToggle={(e) =>
                setOpenMap((m) => ({
                  ...m,
                  [gk]: (e.target as HTMLDetailsElement).open,
                }))
              }
              className="rounded-md border border-hairline"
            >
              <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-2 text-xs text-text-secondary">
                <span className="mono truncate">{g.label}</span>
                <span className="tab-count">{g.items.length}</span>
              </summary>
              <ul className="flex flex-col gap-2 p-2.5 pt-0">
                {g.items.map((a) => (
                  <AttachmentItem key={a.id} ticketKey={ticketKey} attachment={a} />
                ))}
              </ul>
            </details>
          );
        })}
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
