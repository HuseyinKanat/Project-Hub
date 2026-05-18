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
}: MarkdownFieldEditorProps) {
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
    <section className="card p-3 space-y-2">
      <header className="flex items-center justify-between gap-2">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
            {label}
            {required && <span className="ml-1 text-red-500">*</span>}
          </h3>
          {description && <p className="text-xs text-slate-500 dark:text-slate-400">{description}</p>}
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
          {/* Tabs */}
          <div className="flex items-center gap-1 border-b border-slate-200 dark:border-slate-600">
            <button
              type="button"
              onClick={() => setActiveTab("edit")}
              className={cn(
                "px-3 py-1.5 text-xs font-medium transition-colors",
                activeTab === "edit"
                  ? "border-b-2 border-indigo-500 text-slate-800 dark:border-indigo-400 dark:text-slate-200"
                  : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
              )}
            >
              Edit
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("preview")}
              className={cn(
                "px-3 py-1.5 text-xs font-medium transition-colors",
                activeTab === "preview"
                  ? "border-b-2 border-indigo-500 text-slate-800 dark:border-indigo-400 dark:text-slate-200"
                  : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
              )}
            >
              Preview
            </button>
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
            <div className="rounded-md border border-slate-200 bg-white px-3 py-2 dark:border-slate-600 dark:bg-slate-900">
              <MarkdownRenderer content={draft} />
            </div>
          )}

          {error && (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {error}
            </p>
          )}

          <div className="flex items-center justify-between">
            <span className="text-[10px] text-slate-400 dark:text-slate-500">
              Markdown destekleniyor: **kalın**, *italik*, `kod`, [link](url)
            </span>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="btn-ghost text-xs"
                onClick={cancel}
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
        </div>
      ) : empty ? (
        <p
          className={cn(
            "rounded-md border border-dashed px-3 py-4 text-center text-xs",
            required
              ? "border-red-300 bg-red-50 text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400"
              : "border-slate-300 bg-slate-50 text-slate-500 dark:border-slate-600 dark:bg-slate-800/50 dark:text-slate-500",
          )}
        >
          {required ? "Bu alan zorunlu, henüz doldurulmadı." : "Boş"}
        </p>
      ) : (
        <div className="rounded-md border border-slate-200 bg-white px-3 py-2 dark:border-slate-600 dark:bg-slate-900">
          <MarkdownRenderer content={value} />
        </div>
      )}
    </section>
  );
}
