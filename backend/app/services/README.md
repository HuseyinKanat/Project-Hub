# services/

Business logic layer.

**Every mutation must:**
1. Call `require_permission()` first.
2. Acquire row lock if state-changing.
3. Apply the change.
4. Write `TicketHistory` event(s).
5. Commit transaction.
6. **After commit**, publish to `event_bus`.

See `skills.md` § 5 (events) and § 12 (audit).
