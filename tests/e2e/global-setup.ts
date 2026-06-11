/**
 * Playwright globalSetup — PH-137 workflow snapshot.
 *
 * Runs ONCE before any spec in the suite.
 *
 * Steps:
 *  1. Fetch the live PH workflow via GET /api/boards/PH.
 *  2. Compare it against the canonical disk fixture (backend/tests/fixtures/ph_workflow_canonical.json).
 *     If they differ → FAIL FAST with a clear message (prevents silent baseline corruption).
 *  3. Write the live snapshot to tests/e2e/.snapshot.json (gitignored).
 *
 * The snapshot is read by installSnapshotHooks() (beforeAll/afterAll in each spec)
 * and by globalTeardown as the last-resort restore target.
 */

import { request } from "@playwright/test";
import {
  getCanonicalFromDisk,
  normaliseWorkflow,
  writeSnapshot,
  API_BASE,
  BOARD_KEY,
  ADMIN_TOKEN,
  type WorkflowSnapshot,
} from "./helpers/workflowSnapshot";

export default async function globalSetup(): Promise<void> {
  const requestContext = await request.newContext();

  try {
    // 1. Fetch live PH workflow directly
    console.log("[globalSetup] Snapshotting live PH workflow...");
    const res = await requestContext.get(`${API_BASE}/api/boards/${BOARD_KEY}`, {
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
    });
    if (!res.ok()) {
      const msg = `[globalSetup] FAIL: GET /api/boards/${BOARD_KEY} failed: ${res.status()} ${await res.text()}`;
      console.error(msg);
      throw new Error(msg);
    }
    const board = (await res.json()) as {
      workflow?: {
        id?: string;
        states?: WorkflowSnapshot["states"];
        transitions?: WorkflowSnapshot["transitions"];
      };
    };
    const workflowId = board.workflow?.id;
    if (!workflowId) {
      throw new Error("[globalSetup] board has no workflow id");
    }
    const liveSnapshot: WorkflowSnapshot = {
      workflowId,
      states: board.workflow?.states ?? [],
      transitions: board.workflow?.transitions ?? [],
    };
    console.log(
      `[globalSetup] Captured: workflowId=${liveSnapshot.workflowId}, ` +
        `${liveSnapshot.states.length} states, ${liveSnapshot.transitions.length} transitions`,
    );

    // 2. Compare against canonical disk fixture (fail fast on pre-existing drift)
    let canonical: ReturnType<typeof getCanonicalFromDisk> | null = null;
    try {
      canonical = getCanonicalFromDisk();
    } catch (err) {
      console.warn(
        `[globalSetup] Could not read canonical fixture: ${String(err)}. ` +
          "Skipping drift check.",
      );
    }

    if (canonical) {
      const liveNorm = normaliseWorkflow(liveSnapshot.states, liveSnapshot.transitions);
      const canonNorm = normaliseWorkflow(canonical.states, canonical.transitions);

      // Compare state names (fast check)
      const liveStateNames = liveNorm.states.map((s) => s.name).sort();
      const canonStateNames = canonNorm.states.map((s) => s.name).sort();
      const liveStr = JSON.stringify(liveStateNames);
      const canonStr = JSON.stringify(canonStateNames);

      if (liveStr !== canonStr) {
        const msg =
          `[globalSetup] FAIL FAST: Live PH workflow has already drifted from the canonical fixture!\n` +
          `  Canonical states: ${canonStr}\n` +
          `  Live states:      ${liveStr}\n` +
          `\n` +
          `  Fix: run 'docker compose exec backend python -m app.cli update_board_roles'\n` +
          `  or manually restore the workflow before running Playwright.\n` +
          `\n` +
          `  If this is an intentional change, update backend/tests/fixtures/ph_workflow_canonical.json.`;
        console.error(msg);
        throw new Error(msg);
      }

      // Also compare transition counts
      const liveTrCount = liveSnapshot.transitions.length;
      const canonTrCount = canonical.transitions.length;
      if (liveTrCount !== canonTrCount) {
        const msg =
          `[globalSetup] FAIL FAST: Live PH workflow transition count (${liveTrCount}) ` +
          `differs from canonical (${canonTrCount}).\n` +
          `  Likely cause: a previous test run leaked transitions.\n` +
          `  Fix: restore the workflow manually or run the bootstrap CLI.`;
        console.error(msg);
        throw new Error(msg);
      }

      console.log("[globalSetup] Drift check passed — live matches canonical fixture.");
    }

    // 3. Write live snapshot
    writeSnapshot(liveSnapshot);
    console.log(`[globalSetup] Snapshot written to tests/e2e/.snapshot.json`);

    // Verify the backend is reachable (early warning before tests start)
    const healthRes = await requestContext.get(`${API_BASE}/health`, {
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      timeout: 5000,
    }).catch(() => null);
    if (!healthRes || !healthRes.ok()) {
      console.warn(
        "[globalSetup] WARNING: backend health check failed or unreachable. " +
          "Tests that hit the live backend will fail.",
      );
    } else {
      console.log("[globalSetup] Backend health check: OK");
    }
  } finally {
    await requestContext.dispose();
  }
}
