import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import type { Priority, TicketType } from "@/types/api";

interface NewTicketDialogProps {
  boardKey: string;
  open: boolean;
  onClose: () => void;
}

const TYPES: TicketType[] = ["feature", "task", "bug", "epic"];
const PRIORITIES: Priority[] = ["low", "medium", "high", "urgent"];

export function NewTicketDialog({ boardKey, open, onClose }: NewTicketDialogProps) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const titleRef = useRef<HTMLInputElement>(null);

  const [title, setTitle] = useState("");
  const [type, setType] = useState<TicketType>("task");
  const [priority, setPriority] = useState<Priority>("medium");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setTitle("");
      setDescription("");
      setType("task");
      setPriority("medium");
      setError(null);
      setTimeout(() => titleRef.current?.focus(), 0);
    }
  }, [open]);

  const createMut = useMutation({
    mutationFn: () =>
      api.createTicket({
        board_id: boardKey,
        type,
        title: title.trim(),
        description,
        priority,
      }),
    onSuccess: (ticket) => {
      qc.invalidateQueries({ queryKey: ["tickets", boardKey] });
      onClose();
      navigate(`/boards/${boardKey}/tickets/${ticket.key}`);
    },
    onError: (err) => setError((err as Error).message),
  });

  if (!open) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setError(null);
    await createMut.mutateAsync();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <form
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        className="card w-full max-w-md space-y-3 p-4"
      >
        <h2 className="text-lg font-semibold">Yeni ticket</h2>

        <label className="block space-y-1">
          <span className="text-xs font-medium text-slate-600">Başlık</span>
          <input
            ref={titleRef}
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={200}
          />
        </label>

        <div className="grid grid-cols-2 gap-2">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-slate-600">Tip</span>
            <select
              className="input"
              value={type}
              onChange={(e) => setType(e.target.value as TicketType)}
            >
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-slate-600">Öncelik</span>
            <select
              className="input"
              value={priority}
              onChange={(e) => setPriority(e.target.value as Priority)}
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="block space-y-1">
          <span className="text-xs font-medium text-slate-600">Açıklama</span>
          <textarea
            className="input font-mono text-xs"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        {error && (
          <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="btn-ghost text-sm"
            onClick={onClose}
            disabled={createMut.isPending}
          >
            Vazgeç
          </button>
          <button
            type="submit"
            className="btn-primary text-sm"
            disabled={createMut.isPending || title.trim().length === 0}
          >
            {createMut.isPending ? "Oluşturuluyor…" : "Oluştur"}
          </button>
        </div>
      </form>
    </div>
  );
}
