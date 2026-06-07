/**
 * PH-97: useEnsureBoardWorkflow
 *
 * Fires ensure_board_workflow MCP tool on WorkflowEditor mount.
 * If the board's active workflow is shared (or is_default), the server
 * clones it and returns cloned=true — in that case the hook invalidates
 * the board + workflows query keys so the editor renders the new private copy.
 *
 * Permission behaviour: 401/403 responses are caught and silently ignored
 * (read-only roles cannot clone — the server-side mutation guard handles them).
 */

import { useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiRequestError } from "@/api/client";

interface UseEnsureBoardWorkflowOptions {
  boardId: string | undefined;
  boardKey: string;
  /** Callback fired with the toast message when a clone happened. */
  onCloned?: (message: string) => void;
}

export function useEnsureBoardWorkflow({
  boardId,
  boardKey,
  onCloned,
}: UseEnsureBoardWorkflowOptions): void {
  const queryClient = useQueryClient();

  const { mutate } = useMutation({
    mutationFn: (id: string) => api.ensureBoardWorkflow(id),
    onSuccess: (data) => {
      if (data.cloned) {
        // Invalidate so WorkflowEditor gets the cloned workflow's ID/data.
        queryClient.invalidateQueries({ queryKey: ["board", boardKey] });
        queryClient.invalidateQueries({ queryKey: ["workflows", boardKey] });
        onCloned?.(
          "This workflow was shared with other boards. " +
            "A board-specific copy has been created.",
        );
      }
    },
    onError: (err) => {
      // Silently ignore 401/403 — read-only actors cannot clone.
      if (err instanceof ApiRequestError && (err.status === 401 || err.status === 403)) {
        return;
      }
      // All other errors: also silent (not blocking UX; server-side guard
      // is authoritative and will catch shared-workflow mutations).
      console.error("[useEnsureBoardWorkflow] unexpected error:", err);
    },
  });

  // Fire once on mount (boardId change counts as a new mount context).
  useEffect(() => {
    if (boardId) {
      mutate(boardId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boardId]);
}
