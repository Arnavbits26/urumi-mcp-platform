"""Authentication of API keys against the database."""
from __future__ import annotations

from dataclasses import dataclass

from .db import Database
from .security import key_matches, parse_key_prefix


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    role: str
    key_id: str
    key_prefix: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def authenticate(db: Database, token: str | None) -> AuthUser | None:
    if not token:
        return None
    prefix = parse_key_prefix(token)
    if prefix is None:
        return None
    row = await db.fetchrow(
        """SELECT k.id AS key_id, k.key_hash, k.revoked_at, u.id AS user_id, u.email, u.role, u.disabled
           FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.prefix = $1""", prefix)
    if row is None or row["revoked_at"] is not None or row["disabled"]:
        return None
    if not key_matches(token, row["key_hash"]):
        return None
    await db.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = $1 AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
        row["key_id"])
    return AuthUser(str(row["user_id"]), row["email"], row["role"], str(row["key_id"]), prefix)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None
