import type { PhaseSelector } from "./grouping";

/**
 * PhaseSelect — PH-314
 *
 * The shared, purely-presentational phase picker used by BOTH the upload form
 * (`AttachmentUpload`) and the row "edit phase" affordance (`AttachmentItem`). It
 * renders the `<select>` of story phases and, ONLY for the two iteration outcomes,
 * a sibling `<input type="number" min="1">` for N. State (`sel` + `iterN`) is owned
 * by the parent so each consumer controls prefill/reset; composition to the wire
 * slug is done by the parent via `selectorToSlug` (grouping.ts, node:test-covered).
 *
 * `idPrefix` keeps the label/input ids unique when several pickers mount at once
 * (the upload form + one open row editor). `disabled` covers busy state AND the
 * spec-doc guard (AC7): phase is evidence-only, so a spec kind disables the picker.
 */
const PHASE_OPTIONS: { value: PhaseSelector; label: string }[] = [
  { value: "", label: "— faz yok —" },
  { value: "repro", label: "repro" },
  { value: "iter-fail", label: "iter-N-fail" },
  { value: "iter-pass", label: "iter-N-pass" },
  { value: "before", label: "before" },
  { value: "after", label: "after" },
];

/** True when the picker value carries an iteration number (reveals the N input). */
export function isIterSelector(sel: PhaseSelector): boolean {
  return sel === "iter-fail" || sel === "iter-pass";
}

export function PhaseSelect({
  sel,
  iterN,
  onSelChange,
  onIterNChange,
  disabled = false,
  idPrefix,
}: Readonly<{
  sel: PhaseSelector;
  iterN: number;
  onSelChange: (sel: PhaseSelector) => void;
  onIterNChange: (n: number) => void;
  disabled?: boolean;
  idPrefix: string;
}>) {
  const showIter = isIterSelector(sel);
  return (
    <div className="flex items-end gap-2">
      <div className="flex flex-col gap-1">
        <label htmlFor={`${idPrefix}-phase`} className="text-[11px] text-text-muted">
          Faz (opsiyonel)
        </label>
        <select
          id={`${idPrefix}-phase`}
          value={sel}
          disabled={disabled}
          onChange={(e) => onSelChange(e.target.value as PhaseSelector)}
          className="input"
          style={{ height: 30, paddingTop: 0, paddingBottom: 0 }}
        >
          {PHASE_OPTIONS.map((o) => (
            <option key={o.value || "none"} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {showIter && (
        <div className="flex flex-col gap-1">
          <label
            htmlFor={`${idPrefix}-iter-n`}
            className="text-[11px] text-text-muted"
          >
            İterasyon N
          </label>
          <input
            id={`${idPrefix}-iter-n`}
            type="number"
            min={1}
            step={1}
            value={iterN}
            disabled={disabled}
            onChange={(e) => {
              // Clamp to the min=1 invariant — a cleared/invalid field falls back to 1
              // so the composed slug always has a positive iteration number.
              const n = Number.parseInt(e.target.value, 10);
              onIterNChange(Number.isFinite(n) && n >= 1 ? n : 1);
            }}
            className="input"
            style={{ height: 30, width: 72 }}
            aria-label="İterasyon numarası"
          />
        </div>
      )}
    </div>
  );
}
