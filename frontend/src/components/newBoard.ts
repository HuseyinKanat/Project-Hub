// PH-332 — PURE logic for the New-board create form (no React/JSX import so it is
// node:test-runnable via host Node `--test --experimental-strip-types`, matching
// the repo's colocated pure-helper convention: grouping.ts / permissions.ts /
// lib/profile/validation.ts). The .tsx dialog is the ONLY React consumer.

/** Free-form board fields as the controlled inputs hold them (all strings). */
export interface BoardFormFields {
  key: string;
  name: string;
  description: string;
  project_type: string;
}

/** Per-field + form-level error bag the dialog renders. */
export interface BoardFormErrors {
  key?: string;
  name?: string;
  description?: string;
  project_type?: string;
  /** Non-field / whole-form message (403, unmapped 422, network, …). */
  form?: string;
}

// Authoritative server constraints (backend `BoardCreate`, schemas.py:418-421).
// Mirror ONLY what the server enforces — the schema has NO key regex, length only.
export const BOARD_KEY_MAX = 5;
export const BOARD_NAME_MAX = 160;
export const BOARD_DESCRIPTION_MAX = 2000;
export const BOARD_PROJECT_TYPE_MAX = 40;
export const DEFAULT_PROJECT_TYPE = "web_app";

/**
 * Client-side validation mirroring the API (length only — no invented key regex,
 * which would reject server-valid keys, Architect Decision 3). Returns a bag with
 * a message per invalid field; an empty bag ⇒ valid.
 */
export function validateBoardForm(fields: BoardFormFields): BoardFormErrors {
  const errors: BoardFormErrors = {};
  const key = fields.key.trim();
  const name = fields.name.trim();

  if (key.length === 0) errors.key = "Key zorunlu.";
  else if (key.length > BOARD_KEY_MAX)
    errors.key = `Key en fazla ${BOARD_KEY_MAX} karakter.`;

  if (name.length === 0) errors.name = "İsim zorunlu.";
  else if (name.length > BOARD_NAME_MAX)
    errors.name = `İsim en fazla ${BOARD_NAME_MAX} karakter.`;

  if (fields.description.length > BOARD_DESCRIPTION_MAX)
    errors.description = `Açıklama en fazla ${BOARD_DESCRIPTION_MAX} karakter.`;

  if (fields.project_type.length > BOARD_PROJECT_TYPE_MAX)
    errors.project_type = `Proje tipi en fazla ${BOARD_PROJECT_TYPE_MAX} karakter.`;

  return errors;
}

/**
 * The submit-enable gate — mirrors the AUTHORITATIVE required constraints only
 * (key 1..5 && name non-empty), same shape as NewTicketDialog's
 * `title.trim().length===0` guard. Submit stays disabled until this is true.
 */
export function isBoardSubmittable(fields: BoardFormFields): boolean {
  const key = fields.key.trim();
  const name = fields.name.trim();
  return key.length > 0 && key.length <= BOARD_KEY_MAX && name.length > 0;
}

/** Uppercase-normalize hygiene for the key input (Board.key is uppercased server-side). */
export function normalizeBoardKey(raw: string): string {
  return raw.toUpperCase();
}

/**
 * Build the create payload from the form (trim key/name, drop an empty optional
 * description, default project_type). Kept pure so the mutation body is testable.
 */
export function toBoardCreatePayload(fields: BoardFormFields): {
  key: string;
  name: string;
  description?: string;
  project_type?: string;
} {
  const description = fields.description.trim();
  const projectType = fields.project_type.trim();
  return {
    key: fields.key.trim(),
    name: fields.name.trim(),
    ...(description ? { description } : {}),
    project_type: projectType || DEFAULT_PROJECT_TYPE,
  };
}

// FastAPI's DEFAULT 422 envelope (no custom RequestValidationError handler is
// registered in this backend — verified core/exceptions.py only maps
// ProjectHubError): `{ detail: [{ loc: ["body", <field>], msg, type }] }`. So the
// wire `detail` is an ARRAY even though `ApiError.detail` is typed `string`
// (Architect Risk note) — narrow defensively at runtime.
interface FastApiValidationItem {
  loc?: unknown[];
  msg?: string;
  type?: string;
}

const KNOWN_FIELDS: ReadonlyArray<keyof BoardFormFields> = [
  "key",
  "name",
  "description",
  "project_type",
];

/**
 * Map a 422 body onto field-level errors (AC6). Reads `loc` for the trailing
 * field name; unmapped items collapse into a single `form` message so nothing is
 * swallowed. Returns null when the body is not a recognizable validation array
 * (→ caller falls back to the raw message).
 */
export function mapValidationBody(body: unknown): BoardFormErrors | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return null;

  const errors: BoardFormErrors = {};
  const formMsgs: string[] = [];
  for (const raw of detail) {
    const item = raw as FastApiValidationItem;
    const loc = Array.isArray(item.loc) ? item.loc : [];
    const field = loc[loc.length - 1];
    const msg = item.msg ?? "Geçersiz değer.";
    if (
      typeof field === "string" &&
      (KNOWN_FIELDS as readonly string[]).includes(field)
    ) {
      const fld = field as keyof BoardFormFields;
      errors[fld] = errors[fld] ? `${errors[fld]} ${msg}` : msg;
    } else {
      formMsgs.push(msg);
    }
  }
  if (formMsgs.length > 0) errors.form = formMsgs.join(" ");
  return errors;
}

/**
 * Turn a thrown API error into the form error bag (AC4/AC5/AC6). Pure over
 * `(status, body, message)` so it is testable without constructing a real
 * ApiRequestError. Branches:
 *   409 → inline error on the KEY field (duplicate key) — no navigation.
 *   403 → friendly permission message (form-level) — not the raw error.
 *   422 → field-level mapping (falls back to a form-level message if the body
 *         isn't a mappable validation array).
 *   else → raw message at form level.
 */
export function mapApiErrorToForm(
  status: number,
  body: unknown,
  message: string,
): BoardFormErrors {
  if (status === 409) {
    return { key: "Bu key zaten kullanımda." };
  }
  if (status === 403) {
    return {
      form: "Board oluşturma yetkin yok (en az bir board'da admin olmalısın).",
    };
  }
  if (status === 422) {
    const mapped = mapValidationBody(body);
    if (mapped && Object.keys(mapped).length > 0) return mapped;
    return { form: message || "Doğrulama hatası." };
  }
  return { form: message || `Hata (HTTP ${status}).` };
}
