import { Download, FileText } from "lucide-react";

import { api } from "@/api/client";
import type { AttachmentResponse, CommentResponse } from "@/types/api";

import { formatBytes } from "./attachments/grouping";
import { parseCommentMarker } from "./commentMarker";
import { PrVerdictBadge } from "./PrVerdictBadge";
import {
  isPrReviewerHandoff,
  parseSonarSummary,
  parseVerdictCounts,
} from "./prVerdict";
import type { PrVerdictMeta, VerdictCounts } from "./prVerdict";

/**
 * PrReviewPanel.tsx — PRDEV-1
 *
 * The PR-Review summary panel on the ticket-detail page. Shows the verdict heading
 * (PrVerdictBadge lg), then THREE pieces of secondary enrichment that each hide when
 * absent:
 *   • a severity-count row parsed from the LAST pr-reviewer HANDOFF comment
 *     (`parseVerdictCounts` — pure enrichment; hidden when unparseable, AC3);
 *   • a SonarQube one-liner also parsed from that comment (`parseSonarSummary`, AC7);
 *   • a "Tam raporu gör" action opening the latest kind="review" attachment in DocPopup
 *     (the page owns the popup + its under-cap gating; an over-cap report falls back to a
 *     download link here, since DocPopup's fetch pulls the whole blob — AC2).
 * The parent renders this ONLY when a verdict exists (`meta` is non-null), so the panel
 * itself never has to gate on absence.
 */
export function PrReviewPanel({
  ticketKey,
  meta,
  comments,
  report,
  reportOverCap,
  onOpenReport,
}: Readonly<{
  ticketKey: string;
  meta: PrVerdictMeta;
  comments: CommentResponse[];
  /** Latest kind="review" attachment (the full findings report), or null. */
  report: AttachmentResponse | null;
  /** True when `report` is over the inline-preview cap → download instead of popup. */
  reportOverCap: boolean;
  /** Opens `report` in the page-level DocPopup. Only invoked for an under-cap report. */
  onOpenReport: () => void;
}>) {
  // Last pr-reviewer HANDOFF comment body (comments arrive oldest→newest, so scan from
  // the end). Its body feeds the severity + sonar enrichment; null → both rows hide.
  let reviewBody: string | null = null;
  for (let i = comments.length - 1; i >= 0; i--) {
    const c = comments[i];
    if (!c) continue;
    const { marker } = parseCommentMarker(c.body);
    if (isPrReviewerHandoff(marker)) {
      reviewBody = c.body;
      break;
    }
  }

  const counts = parseVerdictCounts(reviewBody);
  const sonar = parseSonarSummary(reviewBody);

  return (
    <section
      className="card"
      style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}
      aria-labelledby="pr-review-panel-title"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="pr-review-panel-title" className="eyebrow" style={{ margin: 0 }}>
          PR İncelemesi
        </h2>
        <PrVerdictBadge meta={meta} size="lg" />
      </div>

      {counts && <SeverityRow counts={counts} />}

      {sonar && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-text-secondary">
          <span className="eyebrow">SonarQube</span>
          <span className="mono text-text-primary">{sonar}</span>
        </p>
      )}

      <ReportAction
        ticketKey={ticketKey}
        report={report}
        reportOverCap={reportOverCap}
        onOpenReport={onOpenReport}
      />
    </section>
  );
}

const SEVERITY_ITEMS: ReadonlyArray<{
  key: keyof VerdictCounts;
  emoji: string;
  label: string;
}> = [
  { key: "critical", emoji: "🔴", label: "critical" },
  { key: "high", emoji: "🟠", label: "high" },
  { key: "medium", emoji: "🟡", label: "medium" },
  { key: "low", emoji: "🔵", label: "low" },
];

/** Severity-grouped finding counts (🔴 critical N / 🟠 high M / …). Emoji decorative;
 *  each item carries a text label + count so it reads for screen readers. */
function SeverityRow({ counts }: Readonly<{ counts: VerdictCounts }>) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1" aria-label="Bulgu sayıları">
      {SEVERITY_ITEMS.map((it) => (
        <li
          key={it.key}
          className="inline-flex items-center gap-1 text-xs text-text-secondary"
          title={`${it.label}: ${counts[it.key]}`}
        >
          <span aria-hidden="true">{it.emoji}</span>
          <span className="mono font-semibold text-text-primary">{counts[it.key]}</span>
          <span className="text-text-muted">{it.label}</span>
        </li>
      ))}
    </ul>
  );
}

/** The full-report affordance: open under-cap in DocPopup, download over-cap, or an
 *  honest "no report yet" note. Mirrors the SpecDocChips over-cap gate. */
function ReportAction({
  ticketKey,
  report,
  reportOverCap,
  onOpenReport,
}: Readonly<{
  ticketKey: string;
  report: AttachmentResponse | null;
  reportOverCap: boolean;
  onOpenReport: () => void;
}>) {
  if (!report) {
    return <p className="text-[11px] text-text-muted">İnceleme raporu eklenmemiş.</p>;
  }
  if (reportOverCap) {
    return (
      <a
        href={api.attachmentContentUrl(ticketKey, report.id, { download: true })}
        download={report.filename}
        title={`${report.filename} — önizleme için çok büyük (${formatBytes(
          report.size_bytes,
        )}), indirmek için tıklayın`}
        aria-label={`İnceleme raporunu indir — önizleme için çok büyük (${formatBytes(
          report.size_bytes,
        )})`}
        className="inline-flex max-w-max items-center gap-1.5 self-start rounded border border-hairline bg-raised px-2 py-1 text-[11px] text-text-muted opacity-80 transition-colors hover:border-accent hover:text-text-secondary"
      >
        <Download className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="mono truncate">Raporu indir</span>
        <span className="shrink-0">· çok büyük</span>
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={onOpenReport}
      className="btn-secondary btn-sm self-start"
      title={report.filename}
    >
      <FileText className="h-3.5 w-3.5" aria-hidden="true" />
      Tam raporu gör
    </button>
  );
}
