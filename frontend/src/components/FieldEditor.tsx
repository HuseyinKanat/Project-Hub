import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

interface FieldEditorProps {
  label: string;
  value: string | null;
  required?: boolean;
  placeholder?: string;
  rows?: number;
  onSave: (next: string | null) => Promise<void>;
  disabled?: boolean;
  description?: string;
}

export function FieldEditor({
  label,
  value,
  required,
  placeholder,
  rows = 5,
  onSave,
  disabled,
  description,
}: FieldEditorProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!editing) setDraft(value ?? "");
  }, [value, editing]);

  const empty = !value || value.trim().length === 0;

  async function save() {
    setSubmitting(true);
    setError(null);
    try {
      const trimmed = draft.trim();
      await onSave(trimmed.length === 0 ? null : trimmed);
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="card p-3 space-y-2">
      <header className="flex items-center justify-between gap-2">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold text-slate-800">
            {label}
            {required && <span className="ml-1 text-red-600">*</span>}
          </h3>
          {description && <p className="text-xs text-slate-500">{description}</p>}
        </div>
        {!editing && !disabled && (
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={() => setEditing(true)}
          >
            {empty ? "Doldur" : "Düzenle"}
          </button>
        )}
      </header>

      {editing ? (
        <div className="space-y-2">
          <textarea
            className="input font-mono text-xs"
            rows={rows}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            autoFocus
          />
          {error && (
            <p className="text-xs text-red-700" role="alert">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn-ghost text-xs"
              onClick={() => {
                setEditing(false);
                setDraft(value ?? "");
                setError(null);
              }}
              disabled={submitting}
            >
              Vazgeç
            </button>
            <button
              type="button"
              className="btn-primary text-xs"
              onClick={save}
              disabled={submitting}
            >
              {submitting ? "Kaydediliyor…" : "Kaydet"}
            </button>
          </div>
        </div>
      ) : empty ? (
        <p
          className={cn(
            "rounded-md border border-dashed px-3 py-4 text-center text-xs",
            required
              ? "border-red-300 bg-red-50 text-red-700"
              : "border-slate-300 bg-slate-50 text-slate-500",
          )}
        >
          {required ? "Bu alan zorunlu, henüz doldurulmadı." : "Boş"}
        </p>
      ) : (
        <pre className="whitespace-pre-wrap rounded-md bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-800">
          {value}
        </pre>
      )}
    </section>
  );
}
