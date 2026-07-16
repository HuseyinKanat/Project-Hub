/**
 * PH-323 — client-side profile field validation.
 *
 * Pure, dependency-free mirrors of the PH-322 backend guards so the Profile page
 * can BLOCK an invalid value BEFORE any REST call (E1: a regex-failing owner_slug
 * disables Save and fires NO PUT /api/profile). node:test-locked (repo convention:
 * pure `.ts` helpers run under Node's built-in runner — no RTL in this repo).
 *
 * These are a courtesy front line, NOT the authority — the backend re-validates and
 * returns a domain 422 (`profile_field_invalid`) / 409 either way. Keep the regex +
 * caps in lock-step with `app/services/owners.py` + `app/services/project_paths.py`.
 */

/** Backend twin: `app/services/owners.py` OWNER_SLUG_RE (`^[a-z0-9][a-z0-9-]{0,19}$`). */
export const OWNER_SLUG_RE = /^[a-z0-9][a-z0-9-]{0,19}$/;
/** ProfileUpdate.owner_slug Field(max_length=20); the regex already caps at 20. */
export const OWNER_SLUG_MAX = 20;
/** Backend twin: `app/services/project_paths.py` _LOCAL_PATH_MAX (VARCHAR(255)). */
export const LOCAL_PATH_MAX = 255;

/**
 * Validate an owner_slug against the backend regex. Returns a human error string
 * when INVALID, or `null` when valid. An empty value is invalid (the PUT body
 * requires min_length=1). The explicit length branch precedes the regex so an
 * over-long-but-otherwise-legal value gets a precise message instead of the
 * generic character complaint.
 */
export function validateOwnerSlug(slug: string): string | null {
  if (slug.length === 0) return "Owner slug is required.";
  if (slug.length > OWNER_SLUG_MAX) {
    return `Owner slug must be at most ${OWNER_SLUG_MAX} characters.`;
  }
  if (!OWNER_SLUG_RE.test(slug)) {
    return "Use lowercase letters, digits and hyphens; must start with a letter or digit.";
  }
  return null;
}

/**
 * Validate a local_path. Empty string is VALID — it is the remove sentinel
 * (backend deletes the row; no DELETE endpoint). Only a >255-char value is blocked
 * client-side (mirrors the backend 422 `profile_field_invalid(local_path)`). The
 * value is otherwise OPAQUE — never checked for existence or shape. Returns an
 * error string when invalid, else `null`.
 */
export function validateLocalPath(path: string): string | null {
  if (path.length > LOCAL_PATH_MAX) {
    return `Local path must be at most ${LOCAL_PATH_MAX} characters.`;
  }
  return null;
}

/**
 * Whether the owner_slug Save action should be ENABLED for a draft value. Enabled
 * ONLY when: the actor is a human (agents derive their owner from the display_name
 * @suffix — `owner_slug` is human-only, an agent PUT → 403), the draft is a valid
 * slug, AND it differs from the persisted column. This is the decision behind E1 —
 * an invalid or unchanged draft can never trigger a PUT (the button is disabled and
 * the submit handler re-checks with `validateOwnerSlug`).
 */
export function canSaveOwnerSlug(
  kind: string,
  persisted: string | null,
  draft: string,
): boolean {
  if (kind !== "human") return false;
  if (validateOwnerSlug(draft) !== null) return false;
  return draft !== (persisted ?? "");
}
