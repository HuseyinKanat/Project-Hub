import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { cn } from "@/lib/utils";
import type { CommentResponse } from "@/types/api";
import { parseCommentMarker } from "./commentMarker";
import type { CommentMarker, CommentMarkerType } from "./commentMarker";
import { resolveRoleChip, CURATED_ROLES } from "./commentRoleChip";

// Avatar (kit `.avatar` / `.avatar.sm`) — mono initials, cyan tint.
export function Avatar({ name, sm }: Readonly<{ name: string; sm?: boolean }>) {
  const initials = name.replace(/^jarwis-/, "").slice(0, 2).toUpperCase() || "?";
  return (
    <span className={cn("avatar", sm && "sm")} title={name} aria-hidden>
      {initials}
    </span>
  );
}

// Role chip (kit `.role-chip`) — mono 11px pill. `resolveRoleChip` (commentRoleChip.ts)
// keeps curated roles on their hand-picked --role-* hue and gives ANY other/new
// role a DETERMINISTIC lane color, so a brand-new Jarwis role never collapses to
// one muted chip (PH-307).
export function RoleChip({ roleHint }: Readonly<{ roleHint: string | null | undefined }>) {
  const role = resolveRoleChip(roleHint);
  if (!role) return null;
  return (
    <span
      className="role-chip"
      style={{ color: role.colorVar, background: `color-mix(in srgb, ${role.colorVar} 14%, transparent)` }}
    >
      {role.label}
    </span>
  );
}

// Per-marker label + accent color. Exhaustive over CommentMarkerType (a missing
// key is a compile error). DEPLOY-FAILED / ESCALATION share the danger hue but
// are disambiguated by their (distinct) TEXT label.
const MARKER_META: Record<CommentMarkerType, { label: string; color: string }> = {
  HANDOFF: { label: "handoff", color: "var(--info)" },
  BLOCKED: { label: "blocked", color: "var(--warning)" },
  RECOVERY: { label: "recovery", color: "var(--success)" },
  DEPLOY: { label: "deploy", color: "var(--accent)" },
  "DEPLOY-FAILED": { label: "deploy failed", color: "var(--danger)" },
  ESCALATION: { label: "escalation", color: "var(--danger)" },
  EVIDENCE: { label: "evidence", color: "var(--role-qa)" },
};

// A single HANDOFF endpoint (from/to). Known roles reuse the curated role-chip
// pill; terminal tokens (done, user, coordinator, epic, …) render as muted mono
// text. NOTE: this uses CURATED_ROLES DIRECTLY (no fallback) on purpose — the
// PH-307 deterministic lane fallback applies ONLY to a comment author's RoleChip,
// never to a handoff terminal token (which must stay muted).
function HandoffEndpoint({ token }: Readonly<{ token: string }>) {
  const role = CURATED_ROLES[token.toLowerCase()];
  if (role) {
    return (
      <span
        className="role-chip"
        style={{ color: role.colorVar, background: `color-mix(in srgb, ${role.colorVar} 14%, transparent)` }}
      >
        {role.label}
      </span>
    );
  }
  return <span className="mono text-text-secondary" style={{ fontSize: 11 }}>{token}</span>;
}

// Type badge (kit `.type-chip`, inline-colored like TicketDetail's TypeChip) plus,
// for HANDOFF, the from→to endpoint chips.
function MarkerBadge({ marker }: Readonly<{ marker: CommentMarker }>) {
  const meta = MARKER_META[marker.type];
  return (
    <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
      <span
        className="type-chip"
        style={{ color: meta.color, background: `color-mix(in srgb, ${meta.color} 16%, transparent)` }}
      >
        {meta.label}
      </span>
      {marker.type === "HANDOFF" && marker.from && marker.to && (
        <span
          className="inline-flex items-center gap-1"
          aria-label={`handoff ${marker.from} to ${marker.to}`}
        >
          <HandoffEndpoint token={marker.from} />
          <span className="text-text-muted" style={{ fontSize: 11 }} aria-hidden>
            →
          </span>
          <HandoffEndpoint token={marker.to} />
        </span>
      )}
    </div>
  );
}

const COLLAPSE_THRESHOLD = 300;

export function CommentCard({ c }: Readonly<{ c: CommentResponse }>) {
  const [expanded, setExpanded] = useState(false);
  // Peel the leading protocol marker; `body` is the remainder (identical to the
  // original when no marker matched → marker-less comments are unchanged).
  const { marker, body } = parseCommentMarker(c.body);
  const isLong = body.length > COLLAPSE_THRESHOLD;
  const displayBody = isLong && !expanded ? body.slice(0, COLLAPSE_THRESHOLD) + "…" : body;
  const hasBody = displayBody.trim().length > 0;

  return (
    <li className="activity-item">
      <Avatar name={c.author.display_name} sm />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="mono text-text-primary" style={{ fontSize: 12 }}>{c.author.display_name}</span>
          <RoleChip roleHint={c.author.agent_role_hint} />
          <span className="mono ml-auto text-text-muted" style={{ fontSize: 11 }}>
            {new Date(c.created_at).toLocaleString()}
            {c.edited_at && <span className="ml-1 italic">(düzenlendi)</span>}
          </span>
        </div>
        {marker && <MarkerBadge marker={marker} />}
        {hasBody && (
          <div className="prose-sm" style={{ fontSize: 12.5 }}>
            <MarkdownRenderer content={displayBody} />
          </div>
        )}
        {isLong && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-1.5 flex items-center gap-0.5 text-[11px] text-accent hover:text-accent-hover hover:underline"
          >
            {expanded ? <><ChevronUp className="h-3 w-3" /> Daha az</> : <><ChevronDown className="h-3 w-3" /> Daha fazla</>}
          </button>
        )}
      </div>
    </li>
  );
}
