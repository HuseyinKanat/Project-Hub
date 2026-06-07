/**
 * LiveStatus.tsx — PH-171 (F2): presentational connection-status pill.
 *
 * Mirrors the design-system StatusPill (primitives.jsx / kit.css `.status-pill`):
 *   live       → text-success bg-success-soft, pulsing dot, "Live"
 *   connecting → text-warning bg-warning-soft, pulsing dot, "Connecting"
 *   off        → text-danger  bg-danger-soft,  static dot,  "Off"
 *
 * PURELY PRESENTATIONAL — prop-driven. The shell renders it with a derived
 * `status` (default "off"); a real app-level WebSocket lifecycle is out of F2
 * scope (the existing useWebSocket hook is per-board). See PH-171 Risk R1.
 */
import { cn } from "@/lib/utils";

export type LiveStatusValue = "live" | "connecting" | "off";

const STATUS_MAP: Record<
  LiveStatusValue,
  { label: string; tone: string; pulse: boolean }
> = {
  live: { label: "Live", tone: "text-success bg-success-soft", pulse: true },
  connecting: { label: "Connecting", tone: "text-warning bg-warning-soft", pulse: true },
  off: { label: "Off", tone: "text-danger bg-danger-soft", pulse: false },
};

export function LiveStatus({
  status = "off",
  title,
}: Readonly<{
  status?: LiveStatusValue;
  /**
   * Optional native `title` tooltip passthrough. Used by BoardDetail to keep
   * the e2e connection-indicator contract (`[title="Live updates active"]`,
   * `[title*="Connecting"]`) green after replacing the ad-hoc inline pill.
   */
  title?: string;
}>) {
  const { label, tone, pulse } = STATUS_MAP[status];

  return (
    <span
      role="status"
      aria-live="polite"
      aria-label={`Connection status: ${label}`}
      title={title}
      className={cn(
        "badge gap-1.5 border border-current/30 text-xs font-medium",
        tone,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "h-1.5 w-1.5 rounded-full bg-current",
          pulse && "motion-safe:animate-pulse",
        )}
      />
      {label}
    </span>
  );
}
