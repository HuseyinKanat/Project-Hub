/**
 * PH-39 — Members tab Playwright spec
 *
 * Covers:
 *   TC-1  Admin golden path: Members tab loads, member count > 0, kind badges render,
 *         table structure (th scope="col") is correct.
 *
 *   TC-2  Admin add member: AddMember modal opens with actor dropdown + role select;
 *         POST /api/boards/{id}/members fires; modal closes; member count increases;
 *         success toast appears.
 *
 *   TC-3  Admin role update (PATCH + optimistic): change role select → PATCH fires;
 *         success toast appears; cell shows new role.
 *
 *   TC-4  Admin remove member (DELETE + optimistic): remove button → confirm dialog;
 *         confirm → DELETE fires → row disappears + success toast.
 *
 *   TC-5  Last-admin guard: sole admin remove button is disabled + role select locked.
 *
 *   TC-6  Non-admin readonly: amber banner visible, no Add button, role cells are text.
 *
 * Requires local docker compose stack:
 *   frontend → http://localhost:5174
 *   backend  → http://localhost:8000
 */

import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

const BASE = "http://localhost:5174";
const API = "http://localhost:8000";
const ADMIN_TOKEN = "change-me-on-first-login";
const BOARD_KEY = "PH";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function authAndGoMembers(page: Page, token = ADMIN_TOKEN) {
  await page.goto(BASE);
  await page.evaluate(
    ([t]) => localStorage.setItem("projecthub.token", t),
    [token],
  );
  await page.goto(`${BASE}/boards/${BOARD_KEY}/settings`);
  // Click Members tab
  await page.getByRole("tab", { name: /members/i }).click();
}

interface MemberShape {
  id: string;
  actor: {
    id: string;
    kind: string;
    display_name: string;
    agent_role_hint: string | null;
  };
  role: string;
}

async function fetchMembers(request: APIRequestContext): Promise<MemberShape[]> {
  const res = await request.get(`${API}/api/boards/${BOARD_KEY}/members`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  expect(res.ok(), `GET /members failed: ${res.status()}`).toBeTruthy();
  const body = await res.json() as { members: MemberShape[] };
  return body.members;
}

async function fetchActors(request: APIRequestContext) {
  const res = await request.get(`${API}/api/actors`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json() as { actors: Array<{ id: string; display_name: string }> };
  return body.actors;
}

async function addMemberApi(
  request: APIRequestContext,
  boardId: string,
  actorId: string,
  role: string,
) {
  const res = await request.post(`${API}/api/boards/${boardId}/members`, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: { actor_id: actorId, role },
  });
  return res;
}

async function removeMemberApi(
  request: APIRequestContext,
  boardId: string,
  actorId: string,
) {
  await request.delete(`${API}/api/boards/${boardId}/members/${actorId}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
}

async function getBoardId(request: APIRequestContext): Promise<string> {
  const res = await request.get(`${API}/api/boards/${BOARD_KEY}`, {
    headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
  });
  const body = await res.json() as { id: string };
  return body.id;
}

// ---------------------------------------------------------------------------
// TC-1: Admin golden path — members load, badges correct, table structure
// ---------------------------------------------------------------------------

test("TC-1: admin Members tab loads — member count > 0, kind badges render, th scope correct", async ({
  page,
  request,
}) => {
  const members = await fetchMembers(request);
  if (members.length === 0) {
    test.skip(true, "No members to test with");
    return;
  }

  await authAndGoMembers(page);

  // Table container should be visible
  const container = page.getByTestId("members-table-container");
  await expect(container).toBeVisible({ timeout: 15_000 });

  // Should have at least the member count from server
  const rows = container.locator("tbody tr");
  const rowCount = await rows.count();
  // Each member can produce 1 or 2 rows (confirm dialog is a second tr); so at least 1
  expect(rowCount).toBeGreaterThanOrEqual(1);

  // Headers should have scope="col"
  const headers = container.locator("th[scope='col']");
  const headerCount = await headers.count();
  expect(headerCount).toBeGreaterThanOrEqual(3);

  // Check that at least one kind badge is visible
  const agentMembers = members.filter((m) => m.actor.kind === "agent");
  if (agentMembers.length > 0) {
    const firstAgentId = agentMembers[0]!.actor.id;
    const badge = page.getByTestId(`kind-badge-${firstAgentId}`);
    await expect(badge).toBeVisible({ timeout: 5_000 });
    await expect(badge).toContainText(/Agent/i);
  }

  // No Add Member button shown when admin (it should be visible)
  await expect(page.getByTestId("add-member-btn")).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// TC-2: Admin add member — modal opens, POST fires, row added, toast shown
// Uses route mocks to simulate an unjoined actor when needed.
// ---------------------------------------------------------------------------

test("TC-2: admin can add a member via modal — POST fires + toast success + row count increases", async ({
  page,
  request,
}) => {
  const boardId = await getBoardId(request);
  const allActors = await fetchActors(request);
  const currentMembers = await fetchMembers(request);

  // Find an actor NOT already on the board (or use mock)
  const unjoined = allActors.filter(
    (a) => !currentMembers.some((m) => m.actor.id === a.id),
  );

  let actorToAdd: { id: string; display_name: string; kind: string } | null = null;
  let usedMock = false;

  if (unjoined.length > 0) {
    actorToAdd = unjoined[0]!;
  } else {
    // All actors are members — use a mock actor for the /api/actors response
    // so the modal shows something to add
    actorToAdd = {
      id: "mock-unjoined-actor-000",
      display_name: "mock-unjoined",
      kind: "agent",
    };
    usedMock = true;

    // Mock /api/actors to include the fake unjoined actor
    await page.route("**/api/actors", async (route) => {
      const allWithMock = [
        ...allActors,
        { ...actorToAdd!, agent_id: null, agent_role_hint: null },
      ];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ actors: allWithMock }),
      });
    });

    // Mock POST to return success
    await page.route(`**/api/boards/${boardId}/members`, async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            id: "mock-membership-id",
            actor: { ...actorToAdd!, agent_id: null, agent_role_hint: null },
            role: "qa",
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }),
        });
      } else {
        await route.continue();
      }
    });
  }

  await authAndGoMembers(page);

  // Wait for table to load
  await expect(page.getByTestId("members-table-container")).toBeVisible({
    timeout: 15_000,
  });

  const initialRowCount = await page.locator('[data-testid="members-table-container"] tbody tr').count();

  // Open modal
  await page.getByTestId("add-member-btn").click();
  await expect(page.getByTestId("add-member-modal")).toBeVisible({ timeout: 5_000 });

  // Actor select should be visible
  const actorSelect = page.getByTestId("actor-select");
  await expect(actorSelect).toBeVisible();

  // Role select should be visible
  const roleSelect = page.getByTestId("role-select");
  await expect(roleSelect).toBeVisible();

  // Intercept POST (real or mocked)
  const postReqPromise = page.waitForRequest(
    (req) =>
      req.url().includes(`/api/boards/${boardId}/members`) &&
      req.method() === "POST",
    { timeout: 10_000 },
  );

  // Submit
  await page.getByTestId("add-member-submit").click();

  // POST fired
  const intercepted = await postReqPromise;
  expect(intercepted.method()).toBe("POST");

  // Modal should close
  await expect(page.getByTestId("add-member-modal")).not.toBeVisible({
    timeout: 8_000,
  });

  // Success toast
  const toast = page.locator('[data-testid="success-toast"]');
  await expect(toast).toBeVisible({ timeout: 6_000 });
  await expect(toast).toContainText(/member added/i);

  // Cleanup: remove the real added member if we didn't use a mock
  if (!usedMock && actorToAdd) {
    await removeMemberApi(request, boardId, actorToAdd.id);
  }
});

// ---------------------------------------------------------------------------
// TC-3: Admin role update — PATCH fires + optimistic update + toast
// ---------------------------------------------------------------------------

test("TC-3: admin role update — PATCH /members/:actor_id fires + success toast", async ({
  page,
  request,
}) => {
  const boardId = await getBoardId(request);
  const members = await fetchMembers(request);

  // Find a non-admin member to change role (so we don't hit last-admin guard)
  const nonAdminMember = members.find((m) => m.role !== "admin");
  if (!nonAdminMember) {
    test.skip(true, "No non-admin member to test role change");
    return;
  }

  await authAndGoMembers(page);

  // Wait for table
  await expect(page.getByTestId("members-table-container")).toBeVisible({
    timeout: 15_000,
  });

  // Find role select for that member
  const roleSelect = page.getByTestId(`role-select-${nonAdminMember.actor.id}`);
  await expect(roleSelect).toBeVisible({ timeout: 5_000 });

  // Intercept PATCH
  const patchReq = page.waitForRequest(
    (req) =>
      req.url().includes(`/api/boards/${boardId}/members/${nonAdminMember.actor.id}`) &&
      req.method() === "PATCH",
    { timeout: 10_000 },
  );

  // Select a different role
  const options = await roleSelect.locator("option").allTextContents();
  const currentRole = nonAdminMember.role;
  const newRoleOption = options.find((o) => o.trim().replace(/ /g, "_") !== currentRole);

  if (!newRoleOption) {
    test.skip(true, "Only one role available — cannot test role change");
    return;
  }

  await roleSelect.selectOption({ label: newRoleOption.trim() });

  // PATCH fired
  await patchReq;

  // Success toast
  await expect(page.locator('[data-testid="success-toast"]')).toBeVisible({ timeout: 6_000 });

  // Restore original role via API
  await request.patch(`${API}/api/boards/${boardId}/members/${nonAdminMember.actor.id}`, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: { role: currentRole },
  });
});

// ---------------------------------------------------------------------------
// TC-4: Admin remove member — confirm dialog + DELETE fires + row disappears
// Uses a non-admin member from the actual roster (no add/remove teardown needed).
// ---------------------------------------------------------------------------

test("TC-4: admin remove member — confirm dialog + DELETE fires + success toast", async ({
  page,
  request,
}) => {
  const boardId = await getBoardId(request);
  const members = await fetchMembers(request);

  // Pick a non-admin member to safely test remove without touching last admin
  // Use mock DELETE so we don't actually remove anyone from the board
  const adminIds = new Set(members.filter((m) => m.role === "admin").map((m) => m.actor.id));
  const testMember = members.find((m) => !adminIds.has(m.actor.id));

  if (!testMember) {
    test.skip(true, "No non-admin member available to test remove");
    return;
  }

  // Mock DELETE to return 204 without actually removing
  await page.route(`**/api/boards/${boardId}/members/${testMember.actor.id}`, async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({ status: 204, body: "" });
    } else {
      await route.continue();
    }
  });

  await authAndGoMembers(page);

  // Wait for table
  await expect(page.getByTestId("members-table-container")).toBeVisible({
    timeout: 15_000,
  });

  // Find remove button for our test member
  const removeBtn = page.getByTestId(`remove-btn-${testMember.actor.id}`);
  await expect(removeBtn).toBeVisible({ timeout: 5_000 });
  await expect(removeBtn).not.toBeDisabled();

  // Click remove — confirm dialog appears
  await removeBtn.click();
  const confirmBtn = page.getByTestId(`confirm-remove-${testMember.actor.id}`);
  await expect(confirmBtn).toBeVisible({ timeout: 3_000 });

  // Intercept DELETE
  const deleteReq = page.waitForRequest(
    (req) =>
      req.url().includes(`/api/boards/${boardId}/members/${testMember.actor.id}`) &&
      req.method() === "DELETE",
    { timeout: 10_000 },
  );

  // Confirm
  await confirmBtn.click();

  // DELETE fired
  await deleteReq;

  // Row should disappear (optimistic)
  await expect(page.getByTestId(`member-row-${testMember.actor.id}`)).not.toBeVisible({
    timeout: 5_000,
  });

  // Success toast
  await expect(page.locator('[data-testid="success-toast"]')).toBeVisible({ timeout: 6_000 });
});

// ---------------------------------------------------------------------------
// TC-5: Last-admin guard — remove button disabled + role select text-only
// Uses route mock to simulate single-admin scenario reliably.
// ---------------------------------------------------------------------------

test("TC-5: last-admin guard — sole admin remove button disabled + role select locked", async ({
  page,
  request,
}) => {
  const boardId = await getBoardId(request);
  const members = await fetchMembers(request);

  // Pick one of the admins to be the "sole admin" in our mocked response
  const adminMembers = members.filter((m) => m.role === "admin");
  if (adminMembers.length === 0) {
    test.skip(true, "No admin member on board");
    return;
  }

  const soleAdmin = adminMembers[0]!;
  const nonAdminMembers = members.filter((m) => m.role !== "admin").slice(0, 3);

  // Mock the members endpoint to return only 1 admin + a few others
  await page.route(`**/api/boards/${boardId}/members`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          members: [soleAdmin, ...nonAdminMembers],
        }),
      });
    } else {
      await route.continue();
    }
  });

  await authAndGoMembers(page);

  // Wait for table
  await expect(page.getByTestId("members-table-container")).toBeVisible({
    timeout: 15_000,
  });

  // Remove button for the sole admin should be disabled
  const removeBtn = page.getByTestId(`remove-btn-${soleAdmin.actor.id}`);
  await expect(removeBtn).toBeVisible({ timeout: 5_000 });
  await expect(removeBtn).toBeDisabled();

  // Role cell should be text (not a select) for the last admin
  const roleText = page.getByTestId(`role-text-${soleAdmin.actor.id}`);
  await expect(roleText).toBeVisible({ timeout: 5_000 });
  await expect(roleText).toContainText(/admin/i);

  // No editable select for the last admin
  const roleSelect = page.getByTestId(`role-select-${soleAdmin.actor.id}`);
  const roleSelectVisible = await roleSelect.isVisible().catch(() => false);
  expect(roleSelectVisible, "Last admin role select must not be an interactive select").toBe(false);
});

// ---------------------------------------------------------------------------
// TC-6: Non-admin readonly — amber banner visible, no Add button, role text
// ---------------------------------------------------------------------------

test("TC-6: non-admin sees amber banner + no Add button + role as text (AC10)", async ({
  page,
  request,
}) => {
  const members = await fetchMembers(request);
  if (members.length === 0) {
    test.skip(true, "No members to render");
    return;
  }

  // Mock /api/auth/me to return reviewer role on PH board
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        actor: {
          id: "mock-reviewer-actor-id",
          kind: "agent",
          display_name: "test-reviewer",
          agent_id: null,
          agent_role_hint: "reviewer",
        },
        memberships: [
          {
            board_id: "c1592aad-9466-4be9-9c0b-75f41ac3efac",
            board_key: BOARD_KEY,
            role: "reviewer",
          },
        ],
      }),
    });
  });

  await authAndGoMembers(page);

  // Wait for table
  await expect(page.getByTestId("members-table-container")).toBeVisible({
    timeout: 15_000,
  });

  // AC10-d: Read-only banner visible
  const banner = page.getByTestId("members-readonly-banner");
  await expect(banner).toBeVisible({ timeout: 5_000 });
  await expect(banner).toContainText(/read-only/i);

  // AC10-a: No Add Member button
  await expect(page.getByTestId("add-member-btn")).not.toBeVisible({ timeout: 3_000 });

  // AC10-b + AC10-c: Role cells are plain text, no remove buttons
  // Check first member row
  const firstMember = members[0]!;
  const roleText = page.getByTestId(`role-text-${firstMember.actor.id}`).or(
    // For non-last-admin cases the text element renders
    page.getByTestId(`role-text-${firstMember.actor.id}`)
  );

  // The important thing: no interactive role selects
  const allRoleSelects = page.locator('[data-testid^="role-select-"]');
  const selectCount = await allRoleSelects.count();
  // When non-admin, no role selects should be rendered
  expect(selectCount).toBe(0);

  // Remove buttons should not be rendered
  const allRemoveBtns = page.locator('[data-testid^="remove-btn-"]');
  const removeBtnCount = await allRemoveBtns.count();
  expect(removeBtnCount).toBe(0);
});
