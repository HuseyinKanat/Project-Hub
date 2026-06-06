import { useEffect, useState } from "react";

import { MarkdownRenderer } from "@/components/MarkdownRenderer";
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
    <section className="card" style={{ padding: 0 }}>
      <div className="field-head">
        <span className="field-title">
          {label}
          {required && <span style={{ color: "var(--warning)" }}> *</span>}
        </span>
        {!editing && !disabled && (
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => setEditing(true)}
          >
            {empty ? "Doldur" : "Düzenle"}
          </button>
        )}
      </div>

      <div className="field-body">
        {description && !editing && (
          <p className="mb-2 text-xs text-text-muted">{description}</p>
        )}

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
              <p className="text-xs text-danger" role="alert">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="btn-ghost btn-sm"
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
                className="btn-primary btn-sm"
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
                ? "border-hairline bg-danger-soft text-danger"
                : "border-hairline bg-inset text-text-muted",
            )}
          >
            {required ? "Bu alan zorunlu, henüz doldurulmadı." : "Boş"}
          </p>
        ) : (
          <MarkdownRenderer content={value ?? ""} />
        )}
      </div>
    </section>
  );
}
