/**
 * Playwright globalTeardown — PH-137 unconditional workflow restore.
 *
 * Runs ONCE after the suite finishes, regardless of pass/fail/crash/SIGINT.
 * (Does NOT run if the Playwright process is SIGKILLed — that's the documented
 * limit of globalTeardown; CI guard is the second line of defence in that case.)
 *
 * Steps:
 *  1. Read tests/e2e/.snapshot.json (written by globalSetup).
 *  2. If .snapshot.json is missing (globalSetup failed), fall back to the
 *     canonical disk fixture (backend/tests/fixtures/ph_workflow_canonical.json)
 *     and resolve the live workflow id first.
 *  3. POST /mcp/call/update_workflow to restore the workflow.
 *  4. On failure: log loudly with a manual recovery curl command and exit non-zero.
 */

import { request } from "@playwright/test";
import {
  readSnapshot,
  getCanonicalFromDisk,
  API_BASE,
  ADMIN_TOKEN,
  BOARD_KEY,
  type WorkflowSnapshot,
} from "./helpers/workflowSnapshot";

export default async function globalTeardown(): Promise<void> {
  console.log("[globalTeardown] Starting unconditional PH workflow restore...");

  const requestContext = await request.newContext();

  try {
    // 1. Determine which snapshot to restore
    let snapshot = readSnapshot();

    if (snapshot) {
      console.log(
        `[globalTeardown] Using runtime snapshot: workflowId=${snapshot.workflowId}, ` +
          `${snapshot.states.length} states`,
      );
    } else {
      console.warn(
        "[globalTeardown] .snapshot.json missing — falling back to canonical disk fixture",
      );
      const canonical = getCanonicalFromDisk();

      // Resolve the live workflow id (required by update_workflow)
      const boardRes = await requestContext.get(`${API_BASE}/api/boards/${BOARD_KEY}`, {
        headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      });
      const board = (await boardRes.json()) as {
        workflow?: { id?: string; states?: WorkflowSnapshot["states"]; transitions?: WorkflowSnapshot["transitions"] };
      };
      const workflowId = board.workflow?.id;
      if (!workflowId) {
        throw new Error("[globalTeardown] could not resolve live workflow id");
      }
      snapshot = { ...canonical, workflowId };
      console.log(
        `[globalTeardown] Disk fallback: workflowId=${snapshot.workflowId} (from live board), ` +
          `${snapshot.states.length} states from fixture`,
      );
    }

    // 2. Restore
    const res = await requestContext.post(`${API_BASE}/mcp/call/update_workflow`, {
      headers: {
        Authorization: `Bearer ${ADMIN_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        workflow_id: snapshot.workflowId,
        fields: {
          states: snapshot.states,
          transitions: snapshot.transitions,
        },
        board_id: BOARD_KEY,
      },
    });
    if (!res.ok()) {
      throw new Error(`update_workflow failed: ${res.status()} ${await res.text()}`);
    }
    console.log("[globalTeardown] Workflow restored successfully.");

  } catch (err) {
    const errorMsg = String(err);
    console.error(
      `[globalTeardown] FAILED to restore PH workflow: ${errorMsg}\n` +
        `\n` +
        `  The live PH board's workflow may still be in a mutated state.\n` +
        `  Manual recovery:\n` +
        `\n` +
        `    # 1. Get the current workflow id:\n` +
        `    curl -s -H "Authorization: Bearer ${ADMIN_TOKEN}" \\\n` +
        `         ${API_BASE}/api/boards/${BOARD_KEY} | jq '.workflow.id'\n` +
        `\n` +
        `    # 2. Restore using the canonical fixture (replace <WORKFLOW_ID>):\n` +
        `    curl -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" \\\n` +
        `         -H "Content-Type: application/json" \\\n` +
        `         -d @backend/tests/fixtures/ph_workflow_canonical.json \\\n` +
        `         "${API_BASE}/mcp/call/update_workflow"\n` +
        `\n` +
        `    # 3. Verify:\n` +
        `    docker compose exec backend pytest backend/tests/test_ph_workflow_canonical.py -v\n`,
    );
    // Exit non-zero so CI notices the failure
    process.exitCode = 1;
  } finally {
    await requestContext.dispose();
  }
}
