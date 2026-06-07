import { useEffect, useState } from "react";

import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { cn } from "@/lib/utils";

interface MarkdownFieldEditorProps {
  label: string;
  value: string | null;
  required?: boolean;
  placeholder?: string;
  rows?: number;
  onSave: (next: string | null) => Promise<void>;
  disabled?: boolean;
  description?: string;
}

type Tab = "edit" | "preview";

export function MarkdownFieldEditor({
  label,
  value,
  required,
  placeholder,
  rows = 10,
  onSave,
  disabled,
  description,
}: Readonly<MarkdownFieldEditorProps>) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("edit");

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
      setActiveTab("edit");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  function cancel() {
    setEditing(false);
    setDraft(value ?? "");
    setError(null);
    setActiveTab("edit");
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
            {/* Tabs — accent-underline idiom (matches Activity tabs) */}
            <div className="flex items-center gap-1 border-b border-hairline" role="tablist" aria-label="Edit veya önizleme">
              {(["edit", "preview"] as Tab[]).map((tab) => {
                const active = activeTab === tab;
                return (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => setActiveTab(tab)}
                    className={cn(
                      "relative px-3 py-1.5 text-xs font-medium transition-colors duration-fast ease-out",
                      active ? "text-accent" : "text-text-secondary hover:text-text-primary"
                    )}
                  >
                    {tab === "edit" ? "Edit" : "Preview"}
                    {active && (
                      <span className="pointer-events-none absolute inset-x-2 -bottom-px h-0.5 rounded bg-accent shadow-glow-cyan-sm" />
                    )}
                  </button>
                );
              })}
            </div>

            {/* Tab Content */}
            {activeTab === "edit" ? (
              <textarea
                className="input font-mono text-xs"
                rows={rows}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={placeholder}
                autoFocus
              />
            ) : (
              <div className="rounded-md border border-hairline bg-inset px-3 py-2">
                <MarkdownRenderer content={draft} />
              </div>
            )}

            {error && (
              <p className="text-xs text-danger" role="alert">
                {error}
              </p>
            )}

            <div className="flex items-center justify-between">
              <span className="text-[10px] text-text-muted">
                Markdown destekleniyor: **kalın**, *italik*, `kod`, [link](url)
              </span>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={cancel}
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
          <MarkdownRenderer content={value} />
        )}
      </div>
    </section>
  );
}
