import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import type { NotificationResponse } from "@/types/api";

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.listNotifications({ limit: 30 }),
    refetchInterval: 5_000,
  });

  useEffect(() => {
    function handleNotificationEvent() {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    }
    window.addEventListener("notification:new", handleNotificationEvent);
    return () => window.removeEventListener("notification:new", handleNotificationEvent);
  }, [queryClient]);

  const markRead = useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const markAll = useMutation({
    mutationFn: () => api.markAllNotificationsRead(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const unread = data?.unread_count ?? 0;
  const notifications = data?.notifications ?? [];

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="btn-ghost relative flex items-center gap-1"
        aria-label="Bildirimler"
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-[10px] font-bold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-50 w-80 rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-800">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2 dark:border-slate-700">
            <span className="text-sm font-medium dark:text-slate-200">Bildirimler</span>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => markAll.mutate()}
                className="text-xs text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
              >
                Tümünü okundu işaretle
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="px-4 py-6 text-center text-sm text-slate-400 dark:text-slate-500">
                Bildirim yok
              </div>
            ) : (
              notifications.map((n: NotificationResponse) => (
                <NotificationItem
                  key={n.id}
                  notification={n}
                  onRead={() => {
                    if (!n.is_read) markRead.mutate(n.id);
                  }}
                  onClose={() => setOpen(false)}
                />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function NotificationItem({
  notification,
  onRead,
  onClose,
}: {
  notification: NotificationResponse;
  onRead: () => void;
  onClose: () => void;
}) {
  const { boardKey } = (() => {
    const parts = notification.ticket_key.split("-");
    return { boardKey: parts[0] };
  })();

  return (
    <Link
      to={`/boards/${boardKey}/tickets/${notification.ticket_key}`}
      className={cn(
        "flex flex-col gap-0.5 px-4 py-3 text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/50",
        !notification.is_read && "bg-blue-50/50 dark:bg-blue-900/10",
      )}
      onClick={() => {
        onRead();
        onClose();
      }}
    >
      <span className={cn("text-slate-800 dark:text-slate-200", !notification.is_read && "font-medium")}>
        {notification.message}
      </span>
      <span className="text-xs text-slate-400 dark:text-slate-500">
        {new Date(notification.created_at).toLocaleString("tr-TR")}
      </span>
    </Link>
  );
}
