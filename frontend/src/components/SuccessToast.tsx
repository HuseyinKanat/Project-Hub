import { useEffect } from "react";
import { CheckCircle, X } from "lucide-react";

interface SuccessToastProps {
  message: string;
  onDismiss: () => void;
  durationMs?: number;
}

export function SuccessToast({ message, onDismiss, durationMs = 4000 }: SuccessToastProps) {
  useEffect(() => {
    const t = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(t);
  }, [message, durationMs, onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="success-toast"
      className="fixed top-4 right-4 z-50 flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-white shadow-lg"
    >
      <CheckCircle className="h-4 w-4 shrink-0" />
      <span className="text-sm">{message}</span>
      <button
        onClick={onDismiss}
        className="ml-2 hover:opacity-80 focus:outline-none focus:ring-2 focus:ring-white/50 rounded"
        aria-label="Dismiss"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
