import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { cn } from "@/lib/utils";
import type { CommentResponse } from "@/types/api";

// Avatar (kit `.avatar` / `.avatar.sm`) — mono initials, cyan tint.
export function Avatar({ name, sm }: Readonly<{ name: string; sm?: boolean }>) {
  const initials = name.replace(/^jarwis-/, "").slice(0, 2).toUpperCase() || "?";
  return (
    <span className={cn("avatar", sm && "sm")} title={name} aria-hidden>
      {initials}
    </span>
  );
}

// Maps an actor's role hint (e.g. "frontend_dev", "qa") to a label + role token.
const ROLE_TOKEN: Record<string, { label: string; color: string }> = {
  admin: { label: "admin", color: "var(--role-admin)" },
  pm: { label: "pm", color: "var(--role-pm)" },
  architect: { label: "arch", color: "var(--role-architect)" },
  backend_dev: { label: "be", color: "var(--role-backend)" },
  backend: { label: "be", color: "var(--role-backend)" },
  frontend_dev: { label: "fe", color: "var(--role-frontend)" },
  frontend: { label: "fe", color: "var(--role-frontend)" },
  reviewer: { label: "rev", color: "var(--role-reviewer)" },
  qa: { label: "qa", color: "var(--role-qa)" },
  orchestrator: { label: "orch", color: "var(--role-orchestrator)" },
};

// Role chip (kit `.role-chip`) — mono 11px pill, role-token colored.
export function RoleChip({ roleHint }: Readonly<{ roleHint: string | null | undefined }>) {
  const role = roleHint ? ROLE_TOKEN[roleHint] : undefined;
  if (!role) return null;
  return (
    <span
      className="role-chip"
      style={{ color: role.color, background: `color-mix(in srgb, ${role.color} 14%, transparent)` }}
    >
      {role.label}
    </span>
  );
}

const COLLAPSE_THRESHOLD = 300;

export function CommentCard({ c }: Readonly<{ c: CommentResponse }>) {
  const [expanded, setExpanded] = useState(false);
  const isLong = c.body.length > COLLAPSE_THRESHOLD;
  const displayBody = isLong && !expanded ? c.body.slice(0, COLLAPSE_THRESHOLD) + "…" : c.body;

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
        <div className="prose-sm" style={{ fontSize: 12.5 }}>
          <MarkdownRenderer content={displayBody} />
        </div>
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
