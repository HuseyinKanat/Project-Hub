"""Token hashing helpers."""

import hashlib

import bcrypt


def hash_token(token: str, rounds: int = 12) -> str:
    """Hash a bearer token for storage."""

    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(token.encode("utf-8"), salt).decode("utf-8")


def verify_token(token: str, token_hash: str) -> bool:
    """Return whether a plaintext token matches a stored hash."""

    return bcrypt.checkpw(token.encode("utf-8"), token_hash.encode("utf-8"))


def token_lookup_digest(token: str) -> str:
    """Deterministic O(1) auth-lookup key for a bearer token (PH-320).

    ``sha256hex(token)`` -- the SINGLE source of truth for the digest shared by
    BOTH the L1 process cache key (``token_cache.VerifiedTokenCache._key``) and
    the L2 DB column ``Actor.token_lookup``, so the in-process cache key is
    byte-identical to the indexed column for any token.

    Unlike the salted bcrypt ``token_hash`` (non-deterministic -> unindexable),
    this digest maps a presented token to exactly ONE indexed row. It is only an
    INDEX KEY, never a credential: the bcrypt ``token_hash`` stays authoritative
    and is still verified on the indexed hit (defense in depth), so a tampered or
    collided ``token_lookup`` cannot authenticate without the real plaintext.
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
