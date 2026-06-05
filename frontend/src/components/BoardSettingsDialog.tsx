import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { BoardResponse, ActorSummary } from "@/types/api";

interface BoardMember {
  actor: ActorSummary;
  role: string;
}

interface BoardSettingsDialogProps {
  board: BoardResponse;
  open: boolean;
  onClose: () => void;
}

export function BoardSettingsDialog({ board, open, onClose }: BoardSettingsDialogProps) {
  const [webhookSecret, setWebhookSecret] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<"webhook" | "members">("webhook");
  const [newMemberEmail, setNewMemberEmail] = useState("");
  const [newMemberRole, setNewMemberRole] = useState("viewer");
  const [isAddingMember, setIsAddingMember] = useState(false);
  const queryClient = useQueryClient();

  // Available roles from the system
  const availableRoles = [
    "admin", "pm", "architect", "frontend_dev", "backend_dev",
    "reviewer", "qa", "unity_dev", "unity_scene_manager", "orchestrator"
  ];

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

  // For now, mock board members since API endpoints don't exist yet
  // This would be replaced with actual API calls
  const mockMembers: BoardMember[] = [
    {
      actor: { id: "1", kind: "human", display_name: "Admin User", agent_id: null, agent_role_hint: null },
      role: "admin"
    },
    {
      actor: { id: "2", kind: "agent", display_name: "jarwis-pm", agent_id: "jarwis-pm", agent_role_hint: "pm" },
      role: "pm"
    }
  ];

  const addMemberMutation = useMutation({
    mutationFn: async (data: { email: string; role: string }) => {
      // TODO: Implement actual API call for adding board members
      // This is a placeholder
      console.log("Adding member:", data);
      return Promise.resolve();
    },
    onSuccess: () => {
      setNewMemberEmail("");
      setNewMemberRole("viewer");
      setIsAddingMember(false);
      queryClient.invalidateQueries({ queryKey: ["board", board.key, "members"] });
    },
    onError: () => {
      setIsAddingMember(false);
    },
  });

  const removeMemberMutation = useMutation({
    mutationFn: async (actorId: string) => {
      // TODO: Implement actual API call for removing board members
      console.log("Removing member:", actorId);
      return Promise.resolve();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["board", board.key, "members"] });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!webhookSecret.trim()) return;
    setIsSaving(true);
    updateMutation.mutate(webhookSecret.trim());
  };

  const handleAddMember = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMemberEmail.trim() || !newMemberRole) return;
    setIsAddingMember(true);
    addMemberMutation.mutate({ email: newMemberEmail.trim(), role: newMemberRole });
  };

  const handleRemoveMember = (actorId: string) => {
    if (window.confirm("Bu üyeyi board'dan kaldırmak istediğinizden emin misiniz?")) {
      removeMemberMutation.mutate(actorId);
    }
  };

  const handleChangeRole = (actorId: string, newRole: string) => {
    // TODO: Implement role change API call
    console.log("Changing role for actor", actorId, "to", newRole);
  };

  const currentSecret = board.roles?.webhook_secret as string | undefined;

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-2xl rounded-lg bg-white shadow-lg">
        <div className="border-b border-slate-200 px-6 py-4">
          <div className="flex items-center justify-between">
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

          {/* Tabs */}
          <div className="mt-4 flex space-x-4">
            <button
              type="button"
              onClick={() => setActiveTab("webhook")}
              className={`pb-2 text-sm font-medium ${
                activeTab === "webhook"
                  ? "border-b-2 border-blue-500 text-blue-600"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              Webhook
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("members")}
              className={`pb-2 text-sm font-medium ${
                activeTab === "members"
                  ? "border-b-2 border-blue-500 text-blue-600"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              Üyeler
            </button>
          </div>
        </div>

        <div className="px-6 py-4">
          {activeTab === "webhook" && (
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
          )}

          {activeTab === "members" && (
            <div className="space-y-6">
              {/* Add new member form */}
              <form onSubmit={handleAddMember} className="space-y-4 rounded-lg border border-slate-200 p-4">
                <h3 className="text-sm font-medium text-slate-900">Yeni Üye Ekle</h3>
                <div className="grid grid-cols-3 gap-4">
                  <div className="col-span-2">
                    <label className="mb-1 block text-sm font-medium text-slate-700">
                      E-posta
                    </label>
                    <input
                      type="email"
                      value={newMemberEmail}
                      onChange={(e) => setNewMemberEmail(e.target.value)}
                      placeholder="kullanici@email.com"
                      className="w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
                      required
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-slate-700">
                      Rol
                    </label>
                    <select
                      value={newMemberRole}
                      onChange={(e) => setNewMemberRole(e.target.value)}
                      className="w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
                    >
                      {availableRoles.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={isAddingMember || !newMemberEmail.trim()}
                  className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {isAddingMember ? "Ekleniyor..." : "Üye Ekle"}
                </button>
              </form>

              {/* Current members list */}
              <div>
                <h3 className="mb-4 text-sm font-medium text-slate-900">Mevcut Üyeler</h3>
                <div className="space-y-2">
                  {mockMembers.map((member) => (
                    <div
                      key={member.actor.id}
                      className="flex items-center justify-between rounded-lg border border-slate-200 p-3"
                    >
                      <div className="flex items-center space-x-3">
                        <div className="flex-shrink-0">
                          <div className={`h-8 w-8 rounded-full flex items-center justify-center text-xs font-medium ${
                            member.actor.kind === 'agent'
                              ? 'bg-blue-100 text-blue-800'
                              : 'bg-slate-100 text-slate-800'
                          }`}>
                            {member.actor.kind === 'agent' ? '🤖' : '👤'}
                          </div>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-slate-900">{member.actor.display_name}</p>
                          <p className="text-xs text-slate-500">
                            {member.actor.kind === 'agent' ? 'Agent' : 'Human'} • {member.role}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center space-x-2">
                        <select
                          value={member.role}
                          onChange={(e) => handleChangeRole(member.actor.id, e.target.value)}
                          className="rounded border border-slate-300 px-2 py-1 text-xs focus:border-slate-500 focus:outline-none"
                        >
                          {availableRoles.map((role) => (
                            <option key={role} value={role}>
                              {role}
                            </option>
                          ))}
                        </select>
                        {member.role !== 'admin' && (
                          <button
                            type="button"
                            onClick={() => handleRemoveMember(member.actor.id)}
                            className="text-red-600 hover:text-red-800"
                            aria-label="Üyeyi kaldır"
                          >
                            🗑️
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
