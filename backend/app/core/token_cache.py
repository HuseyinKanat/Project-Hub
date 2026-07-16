"""Process-local verified-token cache (PH-319 hotfix).

`current_actor` used to scan *every* active actor and run bcrypt
(``checkpw`` ~0.5s at cost factor 12) on each one for **every** authenticated
request -- O(n) bcrypt per call (~12s with 24 actors, measured). This cache maps
a hashed bearer token to the actor it last verified against so a repeat request
skips bcrypt entirely.

Security invariants (these are load-bearing -- do not relax without review):

* **Plaintext is never stored.** The map key is ``sha256(token)``; the raw
  token never enters the cache's internal state.
* **Snapshot binds the entry to a token_hash.** Each entry records the actor's
  ``token_hash`` at insert time. On a hit the caller re-reads the actor row and
  compares; if the hash changed (rotation/revocation) the snapshot mismatches,
  the entry is dropped, and the request falls back to the full bcrypt scan. A
  rotated or revoked token can therefore never authenticate from a stale entry.
* **Bounded + expiring.** Entries expire after ``ttl_seconds`` and the map is
  size-capped with LRU eviction, so a flood of distinct tokens cannot grow the
  process memory without bound.

Wrong tokens are never inserted (only a successful ``verify_token`` writes an
entry), so the 403 path is unaffected and cannot be poisoned.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from app.core.security import token_lookup_digest

_DEFAULT_TTL_SECONDS = 600.0
_DEFAULT_MAX_ENTRIES = 512


@dataclass(frozen=True)
class CacheEntry:
    """A verified-token cache record. ``actor_id``/``token_hash`` are opaque strings."""

    actor_id: str
    token_hash: str
    cached_at: float


class VerifiedTokenCache:
    """Thread-safe, TTL- and size-bounded map: sha256(token) -> CacheEntry.

    A single uvicorn worker runs one asyncio loop, so contention is nil in
    practice; the lock is kept anyway so the invariants hold even under a
    threaded server or test harness.
    """

    def __init__(
        self,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        # Ordered oldest-first so popitem(last=False) evicts the LRU entry.
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()

    @staticmethod
    def _key(token: str) -> str:
        # PH-320: delegate to the single digest source so the L1 cache key is
        # byte-identical to the L2 ``Actor.token_lookup`` column for any token.
        return token_lookup_digest(token)

    def get(self, token: str) -> CacheEntry | None:
        """Return a live entry for ``token``, or ``None`` on miss/expiry.

        A hit refreshes recency (LRU); an expired entry is evicted eagerly.
        """
        key = self._key(token)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if now - entry.cached_at > self._ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return entry

    def put(self, token: str, actor_id: str, token_hash: str) -> None:
        """Record that ``token`` verified against ``actor_id`` (snapshot ``token_hash``)."""
        key = self._key(token)
        with self._lock:
            self._entries[key] = CacheEntry(
                actor_id=actor_id, token_hash=token_hash, cached_at=time.monotonic()
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def invalidate(self, token: str) -> None:
        """Drop the entry for ``token`` if present (idempotent)."""
        key = self._key(token)
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Empty the cache (used by tests and on rotation sweeps)."""
        with self._lock:
            self._entries.clear()


# Process-wide singleton consumed by ``app.api.deps.current_actor``.
verified_token_cache = VerifiedTokenCache()
