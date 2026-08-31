# ADR-0001: Stale-claim release backfills an empty assignee with the expiring claim owner

## Status

Accepted (PH-340)

> Convention (this file establishes `docs/adr/`, which did not exist before PH-340):
> one ADR per file, named `NNNN-kebab-slug.md` (zero-padded 4-digit sequence, first
> record `0001`); H1 is `# ADR-NNNN: <title>`; required sections are **Status /
> Context / Decision / Rationale / Rejected Alternatives / Consequences**; `Status`
> is `Accepted` on the first commit.

## Context

In the Jarwis pilot an agent calls `claim_ticket` but the `assign_ticket` step can be
skipped, so a ticket can legitimately reach `claimed_by = <agent>`, `assignee_id = NULL`
(`services/tickets.py:claim_ticket` sets only `claimed_by`/`claimed_at`, never
`assignee_id`).

`if_assignee`-scoped write authority is granted when
`resource.assignee_id == actor.id OR resource.claimed_by == actor.id`
(`core/permissions.py:_permission_matches`, since commit `806d829`, 2026-05-18 — the
claim owner already counts as assignee). So while the claim is held the agent CAN write
its own `impact_analysis` / `technical_depth`.

The failure appears on a long, heartbeat-silent operation. `CLAIM_TIMEOUT_SECONDS = 300`
(`services/stale_claims.py`); an iOS `xcodebuild`, a `swift test`, or a Docker image build
easily exceeds five minutes without a heartbeat. The stale-claim cron then sets
`claimed_by = None`. If `assignee_id` was also `NULL`, BOTH equalities in
`_permission_matches` now evaluate false and the agent **silently loses write authority
mid-work** — its structured-field write is denied and the content falls into a comment
body instead. The IQB 464-ticket process audit (2026-08-28) measured this on **18 tickets**
(empty structured field, content in comment) and observed stale-claim / `force_release`
on 10 tickets in the same window — two faces of one mechanism.

A second, smaller diagnosis gap: `PermissionDenied(required, have)` carried no reason. A
human reading `have` sees the `ticket.update_field:if_assignee` grant IS present and looks
in the wrong place. (Addressed by the companion AC-1 change — a `reason="not_owner"`
denial field — which is orthogonal to this ADR's storage decision.)

## Decision

The stale-claim release pins an EMPTY assignee to the expiring claim owner:
`release_stale_claims()`, in the SAME transaction and BEFORE nulling `claimed_by`, sets
`assignee_id = claimed_by` **only when `assignee_id IS NULL`**. A non-null assignee is
NEVER overwritten. The `released` history event's `new_value` additionally records
`assignee_id` alongside the existing `reason: "stale_claim_timeout"` so the pin is
auditable.

`_permission_matches()` is NOT changed (the fix reuses the existing `assignee_id == actor`
path). No new column and no Alembic migration are introduced — the existing `assignee_id`
column is reused.

## Rationale

- **Does not touch the ownership predicate.** The most security-critical, pure function
  (`_permission_matches`) is left byte-for-byte identical; the fix reuses its existing
  `assignee_id == actor` branch rather than adding a new ownership source.
- **Covers unbounded duration.** The root cause is operations that EXCEED a timeout; a
  fixed grace window (alternative (a)) merely adds a second timeout that can also be
  exceeded. An assignee pin is time-independent: it holds until the work completes and the
  Coordinator rotates the ticket onward.
- **No new schema.** Reuses the existing `assignee_id` column — no migration, no new
  config knob.
- **No new race class.** Dual ownership (assignee = A while a new claimer = B) is an
  ALREADY-existing property of the OR-based predicate (it also arises when the Coordinator
  sets assignee = X and Y later claims); (b) adds no new ownership source.

## Rejected Alternatives

**(a) Grace mechanism — a `released_from` timestamp column plus a short post-release
window in which the just-released owner still passes.** Rejected:

1. It would widen `_permission_matches()` with a time-windowed `released_from` condition —
   injecting `now()` into a pure, deterministic permission function, hurting testability
   and violating the "change without breaking it" constraint of this ticket.
2. It requires a new column (+ Alembic migration) and a grace-duration config — strictly
   more surface than (b).
3. A fixed window cannot cover an unbounded build: a 20-minute build + a 2-minute grace =
   the bug returns.
4. Race: if B claims while A's grace is still open, there are two concurrent owners; closing
   that needs extra "revoke `released_from` on claim" logic plus residual-window handling.
   (b) introduces no such new logic.

## Consequences

- (+) A long-build implementer keeps its write authority; the 18-ticket silent-loss class
  is closed.
- (+) No code path outside `stale_claims` changes; `_permission_matches` and every existing
  permission test stay green.
- (-, disclosed) **Tension with the Coordinator's assignee authority** (Jarwis
  `roles/coordinator.md` MANDATORY #4: assignee rotation is the Coordinator's). The server
  now writes `assignee` — but only (1) when it is NULL, (2) pinning to the de-facto owner
  (the claim holder), performing NO role rotation, (3) in the same direction as the
  Coordinator's own intent (Jarwis `contracts/transition-map.md` §5b: "the implementer MUST
  be the assignee"), realizing it rather than fighting it, and (4) the Coordinator's next
  transition (implementer done -> in_review + assign reviewer) overwrites this assignee, so
  the server write is transient and rotation-subject. A genuinely abandoned chain is left
  with assignee = last worker, but the stale-claim audit keys on `claimed_by` + heartbeat
  (NOT assignee), so staleness is still detected and the Coordinator reassigns. Net:
  rotation authority is preserved; the server only fills a NULL with the obvious owner in an
  anomalous window.
- (-, low) Dual ownership (ghost assignee A + new claimer B) is a pre-existing property of
  the OR predicate, not a new class; A is heartbeat-dead so it does not write in practice,
  and the first rotation/claim corrects the assignee.
