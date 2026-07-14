/**
 * commentRoleChip.ts — PH-307
 *
 * Pure, JSX-free resolver mapping an actor's `agent_role_hint` (or a HANDOFF
 * endpoint token) to a comment role-chip `{ label, colorVar }`. Extracted from
 * CommentCard.tsx so it can be unit-tested with Node's built-in runner
 * (node:test + native TS strip) — the repo convention (see commentMarker.ts,
 * branchGraphLayout.ts). CommentCard.tsx consumes it for rendering.
 *
 * TWO surfaces, DELIBERATELY different:
 *   • RoleChip (comment author)  → `resolveRoleChip()`: a curated hue when known,
 *     else a DETERMINISTIC lane-color fallback for ANY unknown/new role — so a
 *     brand-new Jarwis role gets a stable, distinct chip instead of collapsing
 *     to one muted pill (the PH-307 fix).
 *   • HandoffEndpoint (from→to)  → `CURATED_ROLES` lookup ONLY (no fallback), so
 *     terminal tokens (done / user / coordinator / epic …) stay muted mono.
 *
 * WHY THE HELPERS ARE INLINED (not imported from `@/lib/utils#hashString` /
 * `@/components/git/branchGraphLayout#laneColor`, the way LabelNode.colorForLabel
 * does): this module is BOTH app-typechecked (tsc, moduleResolution=bundler, NO
 * `allowImportingTsExtensions`) AND loaded by node:test (native strip-types). A
 * single local value-import cannot satisfy both — empirically verified:
 *   - "@/…"           → node:test ERR_MODULE_NOT_FOUND (no tsconfig-path resolver)
 *   - "../x" (no ext) → node:test ERR_MODULE_NOT_FOUND (ESM needs an extension)
 *   - "../x.ts"       → app tsc TS5097 (allowImportingTsExtensions is disabled)
 * So the tiny FNV-1a hash + the `var(--lane-*)` palette are MIRRORED here (the
 * hash already has twin copies in utils.ts / branchGraphLayout.ts). The colocated
 * commentRoleChip.test.ts PINS this mirror to the shared originals (imported there
 * via `.ts`, which node:test allows and the app tsc excludes) → any drift fails.
 */

export interface RoleChipStyle {
  /** Short chip label, e.g. "be", "qa", or a slugged "unity dev". */
  label: string;
  /**
   * A CSS custom-property reference — `var(--role-*)` for a curated role,
   * `var(--lane-*)` for the deterministic fallback. Resolved at paint time so it
   * stays theme-aware (light/dark redefine the vars).
   */
  colorVar: string;
}

/**
 * Curated roles — the 8 hand-picked `--role-*` hues (PRESERVED byte-for-byte
 * from the original CommentCard `ROLE_TOKEN`) plus the bare / `_dev` handoff-token
 * aliases (`backend`/`backend_dev`, `frontend`/`frontend_dev`). Keyed by the
 * server's underscore-verbatim `agent_role_hint` (cli.py).
 *
 * Terminal handoff tokens (done / user / coordinator / epic) are DELIBERATELY
 * absent → HandoffEndpoint renders them muted mono. New implementer roles
 * (unity_*, android_dev, ios_dev, data_*, ml_*) are also absent ON PURPOSE:
 * the 8 curated hues are semantically bound to specific roles, so reusing one
 * would make a new role visually collide with an unrelated one. They fall to
 * the deterministic lane fallback instead, which gives each a stable, distinct
 * hue.
 */
export const CURATED_ROLES: Record<string, RoleChipStyle> = {
  admin: { label: "admin", colorVar: "var(--role-admin)" },
  pm: { label: "pm", colorVar: "var(--role-pm)" },
  architect: { label: "arch", colorVar: "var(--role-architect)" },
  backend_dev: { label: "be", colorVar: "var(--role-backend)" },
  backend: { label: "be", colorVar: "var(--role-backend)" },
  frontend_dev: { label: "fe", colorVar: "var(--role-frontend)" },
  frontend: { label: "fe", colorVar: "var(--role-frontend)" },
  reviewer: { label: "rev", colorVar: "var(--role-reviewer)" },
  qa: { label: "qa", colorVar: "var(--role-qa)" },
  orchestrator: { label: "orch", colorVar: "var(--role-orchestrator)" },
};

/**
 * `coordinator ≡ orchestrator` — Jarwis' Coordinator IS the orchestrator actor.
 * Applied ONLY in `resolveRoleChip` (the comment-author chip), NOT in
 * HandoffEndpoint: a `[HANDOFF coordinator→…]` endpoint stays a muted-mono
 * terminal token (it is intentionally absent from `CURATED_ROLES`).
 */
const ROLE_ALIAS: Record<string, string> = { coordinator: "orchestrator" };

// --- Inlined deterministic fallback (mirror; see file header) --------------
// `var(--lane-*)` palette — BYTE-FOR-BYTE the order of branchGraphLayout.ts
// `LANE_COLORS` (lane 0 = cyan). Kept in sync by commentRoleChip.test.ts, which
// asserts each index against the imported real `laneColor`.
const LANE_COLORS = [
  "var(--lane-cyan)",
  "var(--lane-emerald)",
  "var(--lane-amber)",
  "var(--lane-rose)",
  "var(--lane-violet)",
  "var(--lane-sky)",
  "var(--lane-teal)",
  "var(--lane-indigo)",
] as const;

/** One `var(--lane-*)` per index, cyclic — mirror of branchGraphLayout.laneColor. */
function laneColor(lane: number): string {
  return LANE_COLORS[lane % LANE_COLORS.length]!;
}

/**
 * FNV-1a 32-bit string hash — small, fast, deterministic, no deps. A mirror of
 * lib/utils.hashString (itself a twin of branchGraphLayout's private hashKey);
 * inlined here for node:test self-containment (see file header).
 */
function hashString(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Human slug for an uncurated hint: underscores → spaces (matches MembershipRow). */
function slugRole(key: string): string {
  return key.replace(/_/g, " ");
}

/**
 * Resolve a role hint to a chip style, or `null` when there is no hint.
 *   null / "" / whitespace     → null   (render no chip)
 *   curated (or coordinator)   → the hand-picked `{ label, var(--role-*) }`
 *   ANY other non-empty hint   → DETERMINISTIC `laneColor(hashString % 8)` + slug
 *
 * The fallback guarantees every new / unknown Jarwis role gets a STABLE, DISTINCT
 * hue — never one shared muted chip. Follows the LabelNode.colorForLabel formula
 * (`laneColor(hashString(value) % 8)`) exactly.
 */
export function resolveRoleChip(hint: string | null | undefined): RoleChipStyle | null {
  if (!hint) return null;
  const key = hint.trim().toLowerCase();
  if (!key) return null;
  const curated = CURATED_ROLES[ROLE_ALIAS[key] ?? key];
  if (curated) return curated;
  return { label: slugRole(key), colorVar: laneColor(hashString(key) % 8) };
}
