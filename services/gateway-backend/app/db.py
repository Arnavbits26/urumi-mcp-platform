"""PostgreSQL access (asyncpg) and the schema. Plain SQL on purpose: easy to read and explain."""
from __future__ import annotations

import asyncio
import logging

import asyncpg

log = logging.getLogger("gateway.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  name text NOT NULL DEFAULT '',
  role text NOT NULL CHECK (role IN ('admin','member')),
  disabled boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name text NOT NULL DEFAULT '',
  prefix text UNIQUE NOT NULL,
  key_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);
CREATE TABLE IF NOT EXISTS mcp_servers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  name text NOT NULL,
  url text NOT NULL,
  auth_type text NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none','bearer','header')),
  auth_header_name text,
  credential_enc text,
  enabled boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'unknown',
  status_detail text,
  last_checked_at timestamptz,
  tool_count int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  user_id uuid,
  user_email text NOT NULL,
  key_prefix text,
  server_slug text,
  tool_name text,
  requested_name text NOT NULL,
  duration_ms int NOT NULL,
  outcome text NOT NULL,
  detail text
);
CREATE INDEX IF NOT EXISTS audit_log_ts ON audit_log (ts DESC);
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'mcp';
CREATE TABLE IF NOT EXISTS sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash text UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_seen_at timestamptz
);
CREATE TABLE IF NOT EXISTS tool_rules (
  role text NOT NULL CHECK (role IN ('admin','member')),
  server_slug text NOT NULL,
  tool_name text NOT NULL DEFAULT '*',
  allow boolean NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by text,
  PRIMARY KEY (role, server_slug, tool_name)
);
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self, attempts: int = 30) -> None:
        for attempt in range(1, attempts + 1):
            try:
                self.pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10, command_timeout=15)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("database not ready (%s), attempt %d/%d", type(exc).__name__, attempt, attempts)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("could not connect to the database")
        async with self.pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock(7301)")  # several replicas may start together
            try:
                await conn.execute(SCHEMA)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(7301)")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def fetch(self, q: str, *a):
        return await self.pool.fetch(q, *a)

    async def fetchrow(self, q: str, *a):
        return await self.pool.fetchrow(q, *a)

    async def fetchval(self, q: str, *a):
        return await self.pool.fetchval(q, *a)

    async def execute(self, q: str, *a):
        return await self.pool.execute(q, *a)
