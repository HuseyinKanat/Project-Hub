import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { BoardResponse } from "@/types/api";

interface BoardSettingsDialogProps {
  board: BoardResponse;
  open: boolean;
  onClose: () => void;
}

export function BoardSettingsDialog({ board, open, onClose }: BoardSettingsDialogProps) {
  const [webhookSecret, setWebhookSecret] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const queryClient = useQueryClient();

  const updateMutation = useMutation({
    mutationFn: (secret: string) =>
      api.updateBoard(board.id, { roles: { ...board.roles, webhook_secret: secret } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", board.key] });
      setWebhookSecret("");
      setIsSaving(false);
      onClose();
    },
    onError: () => {
      setIsSaving(false);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!webhookSecret.trim()) return;
    setIsSaving(true);
    updateMutation.mutate(webhookSecret.trim());
  };

  const currentSecret = board.roles?.webhook_secret as string | undefined;

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Board Ayarları</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Kapat"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">
              GitHub Webhook Secret
            </label>
            <p className="mb-2 text-xs text-slate-500">
              HMAC imza doğrulaması için kullanılır. Boş bırakılırsa doğrulama atlanır.
            </p>
            <input
              type="password"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
              placeholder={currentSecret ? "***** (mevcut secret var)" : "Yeni secret girin"}
              className="w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
            />
            {currentSecret && (
              <p className="mt-1 text-xs text-green-600">✓ Webhook secret ayarlanmış</p>
            )}
          </div>

          {updateMutation.isError && (
            <div className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {(updateMutation.error as Error)?.message || "Güncelleme başarısız"}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              İptal
            </button>
            <button
              type="submit"
              disabled={isSaving || !webhookSecret.trim()}
              className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {isSaving ? "Kaydediliyor..." : "Kaydet"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
