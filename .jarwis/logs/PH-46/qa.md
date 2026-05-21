---
ticket: PH-46
role: qa
created: 2026-05-21T10:33:00Z
last_run: 2026-05-21T10:40:00Z
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
