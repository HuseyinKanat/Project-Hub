/**
 * NotificationBell — bell button + glass dropdown panel (PH-187).
 *
 * Panel matches the ui_kit `NotificationPanel` (shell.jsx): glass surface
 * (color-mix bg-raised + hairline-cyan + shadow-lg + radius-lg), pop-in anim,
 * a "N new" eyebrow, and per-item leading semantic icons chosen by
 * `event_type` with a message-keyword fallback. Data plumbing
 * (listNotifications, mark-read, mark-all, unread badge, notification:new
 * invalidation, ticket Link nav) is unchanged from F2/PH-171.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  GitMerge,
  MessageSquare,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "@/api/client";
import { relativeTime } from "@/lib/time";
import { cn } from "@/lib/utils";
import type { NotificationResponse } from "@/types/api";

/**
 * Pick a leading semantic icon + CSS color var for a notification, driven by
 * `event_type` (the authoritative discriminator), with a message-keyword
 * fallback for unknown/empty types. Always returns a valid pair (never throws).
 */
function iconFor(n: NotificationResponse): { Icon: LucideIcon; color: string } {
  switch (n.event_type) {
    case "state_changed":
      return { Icon: Activity, color: "var(--success)" };
    case "comment_added":
      return { Icon: MessageSquare, color: "var(--text-secondary)" };
    case "git_pr_merged":
    case "git_commit_linked":
    case "git_pr_linked":
      return { Icon: GitMerge, color: "var(--accent)" };
    default:
      // Unknown / empty event_type → infer from the message, else neutral bell.
      if (/merged|→\s*main/i.test(n.message)) {
        return { Icon: GitMerge, color: "var(--accent)" };
      }
      return { Icon: Bell, color: "var(--text-muted)" };
  }
}

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
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-2xs font-bold text-text-on-accent ring-2 ring-surface">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className="animate-pop absolute right-0 top-9 z-50 overflow-hidden"
          style={{
            width: 340,
            background: "color-mix(in srgb, var(--bg-raised) 96%, transparent)",
            border: "1px solid var(--hairline-cyan)",
            borderRadius: "var(--radius-lg)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
            <div className="flex items-baseline gap-2">
              <strong className="text-[13px] font-semibold text-text-primary">
                Bildirimler
              </strong>
              {unread > 0 && <span className="eyebrow">{unread} new</span>}
            </div>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => markAll.mutate()}
                className="text-xs text-text-muted transition-colors hover:text-accent"
              >
                Tümünü okundu işaretle
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="px-4 py-6 text-center text-sm text-text-muted">
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
  const boardKey = notification.ticket_key.split("-")[0];
  const { Icon, color } = iconFor(notification);

  return (
    <Link
      to={`/boards/${boardKey}/tickets/${notification.ticket_key}`}
      className={cn(
        "flex gap-[11px] border-b border-hairline px-4 py-3 transition-colors hover:bg-raised",
        !notification.is_read && "bg-accent-subtle",
      )}
      onClick={() => {
        onRead();
        onClose();
      }}
    >
      <span style={{ color, marginTop: 1 }} aria-hidden="true">
        <Icon size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "text-[13px] text-text-primary",
            !notification.is_read && "font-medium",
          )}
        >
          {notification.message}
        </div>
        <div className="mono mt-0.5 text-[11px] text-text-muted">
          {relativeTime(notification.created_at)}
        </div>
      </div>
    </Link>
  );
}
