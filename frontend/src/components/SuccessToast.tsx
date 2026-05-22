/**
 * Backward-compat re-export shim (PH-100).
 * New code should import from "./Toast" directly.
 * This file stays to avoid breaking existing imports in TicketDetail/WorkflowEditor.
 */
export { SuccessToast } from "./Toast";
