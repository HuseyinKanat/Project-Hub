import { useEffect } from "react";
import { CheckCircle, AlertCircle, X } from "lucide-react";

export type ToastVariant = "success" | "error";

interface ToastProps {
  message: string;
  onDismiss: () => void;
  variant?: ToastVariant;
  durationMs?: number;
}

/**
 * Generic Toast component with success/error variants.
 * - success: green background, CheckCircle icon, role="status", aria-live="polite"
 * - error:   red background, AlertCircle icon, role="alert", aria-live="assertive"
 * Auto-dismisses after `durationMs` (default 5000ms).
 */
export function Toast({ message, onDismiss, variant = "success", durationMs = 5000 }: ToastProps) {
  useEffect(() => {
    const t = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(t);
  }, [message, durationMs, onDismiss]);

  const isError = variant === "error";

  return (
    <div
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      data-testid={variant === "success" ? "success-toast" : "toast"}
      data-toast="true"
      data-variant={variant}
      className={`fixed top-4 right-4 z-50 flex items-center gap-2 rounded-lg px-4 py-2 text-white shadow-lg ${
        isError ? "bg-red-600" : "bg-green-600"
      }`}
    >
      {isError ? (
        <AlertCircle className="h-4 w-4 shrink-0" />
      ) : (
        <CheckCircle className="h-4 w-4 shrink-0" />
      )}
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

/**
 * Backward-compat shim: existing callers using `SuccessToast` continue to work
 * without any change. BoardDetail has its own inline toast (not this component).
 * TicketDetail and WorkflowEditor clone-toast use this shim.
 */
export function SuccessToast(props: Omit<ToastProps, "variant">) {
  return <Toast {...props} variant="success" />;
}
