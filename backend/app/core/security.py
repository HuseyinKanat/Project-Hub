"""Token hashing helpers."""

import bcrypt


def hash_token(token: str, rounds: int = 12) -> str:
    """Hash a bearer token for storage."""

    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(token.encode("utf-8"), salt).decode("utf-8")


def verify_token(token: str, token_hash: str) -> bool:
    """Return whether a plaintext token matches a stored hash."""

    return bcrypt.checkpw(token.encode("utf-8"), token_hash.encode("utf-8"))
