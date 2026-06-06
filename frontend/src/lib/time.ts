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
