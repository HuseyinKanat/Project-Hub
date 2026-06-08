/**
 * time.ts — shared time formatting helpers.
 *
 * `relativeTime` was extracted from BranchGraph.tsx (PH-187) so both the
 * branch graph commit rows and the NotificationBell panel share one
 * implementation (DRY). Output matches the ui_kit: "45s/8m/1h/3d/2mo/1y ago".
 */

/** Human-readable relative time, e.g. "45s ago", "8m ago", "3d ago". */
export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

/**
 * Null-safe Turkish relative time via Intl.RelativeTimeFormat, e.g.
 * "3 dakika önce", "2 gün önce". Returns "hiç" for null (never synced).
 *
 * Lifted out of RepositoryStatusPanel (PH-225 / C5) so the multi-repo
 * RepositoryList rows and the legacy status panel share ONE implementation
 * (DRY — avoids the S4144 copy-paste the architect flagged). The repository
 * settings surface uses the Turkish locale to match the rest of the tab copy;
 * the English `relativeTime` above is kept for the branch-view / notifications.
 */
export function humaniseRelativeTr(iso: string | null): string {
  if (!iso) return "hiç";
  const then = new Date(iso);
  const diffMs = Date.now() - then.getTime();
  const diffSec = Math.round(diffMs / 1000);
  const rtf = new Intl.RelativeTimeFormat("tr", { numeric: "auto" });
  if (Math.abs(diffSec) < 60) return rtf.format(-diffSec, "second");
  const diffMin = Math.round(diffSec / 60);
  if (Math.abs(diffMin) < 60) return rtf.format(-diffMin, "minute");
  const diffHr = Math.round(diffMin / 60);
  if (Math.abs(diffHr) < 24) return rtf.format(-diffHr, "hour");
  const diffDay = Math.round(diffHr / 24);
  return rtf.format(-diffDay, "day");
}
