"""Async per-digest single-flight for auth resolution (PH-320).

The board page fires ~5 API calls at once, all carrying the SAME bearer token.
After a backend restart the L1 cache (PH-319) is cold, so all 5 MISS together and
each would run the expensive resolve (indexed lookup + one bcrypt, or the legacy
NULL-restricted scan) concurrently -- and bcrypt serializes on the GIL, so the
burst finishes ~N x slower. This helper collapses concurrent resolves that share
a digest to ONE: the first caller (the leader) runs the body once, the rest await
the shared future and reuse its result.

A single uvicorn worker runs one asyncio event loop, so the get-or-create-future
step is atomic between awaits (the standard asyncio single-flight idiom -- there
is no ``await`` between the ``.get`` miss and the ``[digest] = future`` insert, so
two coroutines can never both become the leader). A defensive ``asyncio.Lock`` is
therefore unnecessary.

CRITICAL -- the future carries only the resolved ``actor_id`` string (or ``None``
-> 403), NEVER a session-bound ORM ``Actor``: the leader's instance belongs to the
leader's request session, and a follower reusing it would raise MissingGreenlet /
DetachedInstanceError. Every waiter re-materialises the ``Actor`` through its OWN
session. The in-flight map holds only the digests currently being resolved (the
leader pops its key in ``finally``), so it is bounded by concurrency, not by the
number of distinct tokens observed over time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class AuthSingleFlight:
    """Deduplicate concurrent async resolves keyed by a digest string."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[str | None]] = {}

    async def resolve(
        self, digest: str, resolver: Callable[[], Awaitable[str | None]]
    ) -> str | None:
        """Return ``resolver()``'s result, running it at most once per in-flight digest.

        The FIRST caller for a not-in-flight ``digest`` becomes the leader: it
        registers a shared future, awaits ``resolver()`` exactly once, publishes
        the result, and pops the key. Concurrent callers for the same digest
        instead await that future and receive the identical result (or the same
        raised exception) WITHOUT re-running ``resolver`` -- so their own
        ``resolver`` closures (and the sessions they capture) are never invoked.
        """
        existing = self._inflight.get(digest)
        if existing is not None:
            return await existing

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()
        # No await between the miss above and this insert -> leader election is
        # atomic on a single event loop.
        self._inflight[digest] = future
        try:
            result = await resolver()
        except BaseException as exc:
            future.set_exception(exc)
            # Mark retrieved so a leader with no followers does not trigger the
            # asyncio "Future exception was never retrieved" warning.
            future.exception()
            raise
        else:
            future.set_result(result)
            return result
        finally:
            # Pop AFTER publishing the result: awaiting followers already hold the
            # future object, so removing the key only stops NEW callers from
            # joining this (now-settled) flight -- they become a fresh leader
            # (by then L1 is warm, so they short-circuit before reaching here).
            self._inflight.pop(digest, None)


# Process-wide singleton consumed by ``app.api.deps.current_actor``.
auth_single_flight = AuthSingleFlight()
