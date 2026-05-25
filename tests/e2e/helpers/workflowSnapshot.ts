/**
 * Shared workflow snapshot/restore helper for PH-137.
 *
 * All 6 in-scope Playwright specs that mutate the live PH workflow MUST call
 * `installSnapshotHooks(test)` at their top level.  This provides:
 *
 *   - beforeAll: restores workflow from the .snapshot.json written by globalSetup
 *   - afterAll:  restores workflow again (safety net for per-spec crashes)
 *
 * The globalTeardown script provides the final unconditional restore for whole-
 * suite crashes (e.g. a test throws and afterAll is skipped).
 *
 * See tests/e2e/README.md for the convention.
 */

import * as fs from "fs";
import * as path from "path";
import type { APIRequestContext, TestType } from "@playwright/test";

// ---------------------------------------------------------------------------
// Constants — single source of truth (AC-6)
// ---------------------------------------------------------------------------

/** PH board key used throughout tests. */
export const BOARD_KEY = "PH";

/** Admin token provisioned at first launch.
 *  This is the initial token for local development — not a secret. */
export const ADMIN_TOKEN = "change-me-on-first-login";

/** Backend base URL. */
export const API_BASE = "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WorkflowState {
  name: string;
  color?: string;
  category?: string;
  is_initial?: boolean;
  is_terminal?: boolean;
  position?: { x: number; y: number };
}

export interface WorkflowTransition {
  from: string;
  to: string;
  allowed_roles?: string[];
  field_gates?: {
    required_fields?: string[];
    exempt_ticket_types?: string[];
  };
}

export interface WorkflowSnapshot {
  workflowId: string;
  states: WorkflowState[];
  transitions: WorkflowTransition[];
}

// ---------------------------------------------------------------------------
// Path to the runtime snapshot file (written by globalSetup, read by hooks)
// ---------------------------------------------------------------------------
const SNAPSHOT_PATH = path.join(__dirname, "..", ".snapshot.json");

// Path to the disk canonical fixture (fallback for globalTeardown)
const CANONICAL_FIXTURE_PATH = path.join(
  __dirname,
  "..",
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "ph_workflow_canonical.json",
);

// ---------------------------------------------------------------------------
// snapshotWorkflow — fetch live PH workflow via REST API
// ---------------------------------------------------------------------------

/**
 * Fetch the live PH board workflow via GET /api/boards/PH.
 * Returns { workflowId, states, transitions }.
 */
export async function snapshotWorkflow(
  request: APIRequestContext,
): Promise<WorkflowSnapshot> {
  const res = await request.get(`${API_BASE}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  if (!res.ok()) {
    throw new Error(
      `snapshotWorkflow: GET /api/boards/${BOARD_KEY} failed: ${res.status()} ${await res.text()}`,
    );
  }
  const board = (await res.json()) as {
    workflow?: {
      id?: string;
      states?: WorkflowState[];
      transitions?: WorkflowTransition[];
    };
  };
  const workflowId = board.workflow?.id;
  if (!workflowId) {
    throw new Error("snapshotWorkflow: board has no workflow id");
  }
  return {
    workflowId,
    states: board.workflow?.states ?? [],
    transitions: board.workflow?.transitions ?? [],
  };
}

// ---------------------------------------------------------------------------
// restoreWorkflow — POST the snapshot back via MCP update_workflow
// ---------------------------------------------------------------------------

/**
 * Restore the PH workflow to a previously-captured snapshot.
 * Uses POST /mcp/call/update_workflow — same path the WorkflowEditor uses.
 * Idempotent: safe to call repeatedly.
 */
export async function restoreWorkflow(
  request: APIRequestContext,
  snapshot: WorkflowSnapshot,
): Promise<void> {
  const res = await request.post(
    `${API_BASE}/mcp/call/update_workflow`,
    {
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
    },
  );
  if (!res.ok()) {
    throw new Error(
      `restoreWorkflow: update_workflow failed: ${res.status()} ${await res.text()}`,
    );
  }
}

// ---------------------------------------------------------------------------
// getCanonicalFromDisk — last-resort baseline for globalTeardown
// ---------------------------------------------------------------------------

/**
 * Read the canonical PH workflow fixture from disk.
 * Used by globalTeardown as fallback when .snapshot.json is missing or stale.
 *
 * The fixture was captured at backend commit 1 (PH-137) and locked as the
 * source of truth.  Any intentional workflow evolution MUST update this file.
 */
export function getCanonicalFromDisk(): WorkflowSnapshot {
  if (!fs.existsSync(CANONICAL_FIXTURE_PATH)) {
    throw new Error(
      `getCanonicalFromDisk: fixture not found at ${CANONICAL_FIXTURE_PATH}`,
    );
  }
  const raw = JSON.parse(fs.readFileSync(CANONICAL_FIXTURE_PATH, "utf-8")) as {
    states?: WorkflowState[];
    transitions?: WorkflowTransition[];
  };

  // The fixture does not include workflowId (it's a shape-only snapshot).
  // globalTeardown resolves the live workflow id before calling restoreWorkflow.
  return {
    workflowId: "", // caller must fill in the live id before restoring
    states: raw.states ?? [],
    transitions: raw.transitions ?? [],
  };
}

// ---------------------------------------------------------------------------
// writeSnapshot / readSnapshot — used by globalSetup/Teardown
// ---------------------------------------------------------------------------

/** Write a WorkflowSnapshot to the runtime snapshot file. */
export function writeSnapshot(snapshot: WorkflowSnapshot): void {
  fs.writeFileSync(SNAPSHOT_PATH, JSON.stringify(snapshot, null, 2), "utf-8");
}

/** Read the runtime snapshot file. Returns null if missing. */
export function readSnapshot(): WorkflowSnapshot | null {
  if (!fs.existsSync(SNAPSHOT_PATH)) return null;
  return JSON.parse(fs.readFileSync(SNAPSHOT_PATH, "utf-8")) as WorkflowSnapshot;
}

// ---------------------------------------------------------------------------
// normaliseWorkflow — sort states + transitions for comparison
// ---------------------------------------------------------------------------

interface NormalisedWorkflow {
  states: WorkflowState[];
  transitions: WorkflowTransition[];
}

/** Normalise workflow for comparison: sort states by name, transitions by (from, to). */
export function normaliseWorkflow(
  states: WorkflowState[],
  transitions: WorkflowTransition[],
): NormalisedWorkflow {
  const sortedStates = [...states].sort((a, b) => a.name.localeCompare(b.name));
  const sortedTransitions = [...transitions].sort((a, b) => {
    const af = a.from + "|" + a.to;
    const bf = b.from + "|" + b.to;
    return af.localeCompare(bf);
  });
  return { states: sortedStates, transitions: sortedTransitions };
}

// ---------------------------------------------------------------------------
// installSnapshotHooks — the one-liner each spec calls
// ---------------------------------------------------------------------------

/**
 * Register beforeAll + afterAll hooks on the given test object that
 * snapshot-restore the PH workflow before and after each spec file.
 *
 * Usage (top-level in each in-scope spec):
 *
 *   import { installSnapshotHooks } from "./helpers/workflowSnapshot";
 *   installSnapshotHooks(test);
 *
 * The beforeAll reads the .snapshot.json written by globalSetup and pushes it
 * to the live backend.  The afterAll does the same — double safety net so even
 * if a test crashes mid-way the workflow is restored before the next spec runs.
 *
 * If .snapshot.json is missing (e.g. globalSetup did not run), the canonical
 * disk fixture is used as the fallback baseline.
 */
export function installSnapshotHooks(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  testObj: TestType<any, any>,
): void {
  testObj.beforeAll(async ({ request }) => {
    let snapshot = readSnapshot();
    if (!snapshot) {
      // Fallback: use disk canonical (globalSetup did not run)
      console.warn(
        "[workflowSnapshot] .snapshot.json not found — using canonical disk fixture as baseline",
      );
      const canonical = getCanonicalFromDisk();
      // Resolve live workflow id
      const live = await snapshotWorkflow(request);
      snapshot = { ...canonical, workflowId: live.workflowId };
    }
    await restoreWorkflow(request, snapshot);
  });

  testObj.afterAll(async ({ request }) => {
    let snapshot = readSnapshot();
    if (!snapshot) {
      console.warn(
        "[workflowSnapshot] afterAll: .snapshot.json missing — using canonical disk fixture",
      );
      const canonical = getCanonicalFromDisk();
      const live = await snapshotWorkflow(request);
      snapshot = { ...canonical, workflowId: live.workflowId };
    }
    await restoreWorkflow(request, snapshot);
  });
}
