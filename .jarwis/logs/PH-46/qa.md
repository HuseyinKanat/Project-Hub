---
ticket: PH-46
role: qa
created: 2026-05-21T10:33:00Z
last_run: 2026-05-21T11:52:00Z
---

## 2026-05-21 10:33 — bug reproduce (Mod A)

- Steps tried:
  1. `docker compose ps` — all services Up (backend :8000, frontend :5174, postgres, redis)
  2. `docker compose logs frontend --tail 30` — confirmed `[vite] ws proxy error: Error: read ECONNRESET` entries at 10:05 and 10:31
  3. Verified `frontend/.env` contains only `VITE_API_URL=http://localhost:8000` — no `VITE_WS_URL`
  4. Ran diagnostic Playwright spec — confirmed app opens `ws://localhost:5174/ws/boards/PH?token=...` (Vite proxy route) and 10+ rapid reconnect attempts in 5s with 0 frames received
- Failing tests: `tests/e2e/websocket-vite-bypass.spec.ts` (4 TCs)
  - TC-01: wsOpenCount=10 in 5s (expected ≤2) — reconnect loop confirmed
  - TC-02: wsClosedEarly=true — connection closes before any frames received  
  - TC-03: WS URL is ws://localhost:5174 (Vite proxy) not :8000 (direct backend)
  - TC-04: ws1Count=8, ws2Count=7 in 5s (expected ≤2 each) — both tabs in loop
- Branch: ph-46-websocket-1006-caused-by-vite-5-x-proxy-econnreset
- Commit: 73b5d19

**Outcome:** 4/4 failing tests committed on branch ph-46-*; test_plan updated; assigned frontend for fix

---

## 2026-05-21 11:52 — verify (Mod B)

- Branch: ph-46-websocket-1006-caused-by-vite-5-x-proxy-econnreset (commits: 73b5d19..daa4521)
- Tests run: `tests/e2e/websocket-vite-bypass.spec.ts` (primary, 4 TCs) + 3 regression specs
- Primary results: 4/4 PASS (was 4/4 FAIL pre-fix)
  - TC-01: WS opens without 1006, no reconnect loop in 5s — PASS
  - TC-02: WS stays open 15s, server messages received — PASS
  - TC-03: WS URL is ws://localhost:8000 (backend direct), no ECONNRESET in logs — PASS
  - TC-04: Multi-tab, both contexts maintain WS without reconnect loop — PASS
- Regression:
  - websocket-token-consistency.spec.ts: 2/3 PASS (1 pre-existing fail: `data-testid="ticket-key-link"` selector; 0/3 on main, 2/3 on PH-46 branch — improvement)
  - ws-live-update.spec.ts: 0/2 PASS (pre-existing: `a[href*='/boards/PH/tickets/']` selector not found; identical on main)
  - test_ph43_websocket_jarwis_regression.spec.ts: 1/3 PASS (2 pre-existing; TC-1 "should fail with 1006" now correctly fails assertion because WS connects successfully — expected, bug is fixed; TC-3 uses 35s waitForTimeout in 30s test timeout — pre-existing design issue)
- AC-9 HMR: touch /app/src/main.tsx triggered `[vite] page reload src/main.tsx` — PASS, no errors
- No new failures introduced by PH-46 changes (verified by comparing main baseline)

**Outcome:** PASSED. test_plan field updated. Handoff comment posted. Coordinator to transition done.
