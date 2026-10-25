"""Counters shared by every gateway replica, kept in PostgreSQL.

With one gateway process, a request count can live in memory. With two or more replicas, each would count
separately and a "120 a minute" limit would quietly become 240. So when RATE_LIMIT_STORE=postgres, the
sliding-window counts live in one table (rate_hits) that every replica reads and writes.

Two details make this correct and not just shared:
  * One transaction takes an advisory lock on the key, then counts, then records. Without the lock, two
    replicas could both read "9 of 10" and both let a request through.
  * Time comes from the database clock, so replicas whose clocks disagree still agree on the window.
"""
from __future__ import annotations

import math

from .db import Database


class PgWindows:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._calls = 0

    async def check_and_add(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        """Count this request unless the key is already at its limit. Returns (allowed, retry_after_seconds)."""
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
                await conn.execute("DELETE FROM rate_hits WHERE key = $1 AND ts < clock_timestamp() - make_interval(secs => $2)", key, window)
                row = await conn.fetchrow(
                    "SELECT count(*) AS n, EXTRACT(EPOCH FROM (clock_timestamp() - min(ts))) AS age FROM rate_hits WHERE key = $1", key)
                if row["n"] >= limit:
                    return False, max(1, math.ceil(window - float(row["age"])))
                await conn.execute("INSERT INTO rate_hits (key) VALUES ($1)", key)
        await self._sweep()
        return True, 0

    async def count(self, key: str, window: float) -> int:
        return await self.db.fetchval(
            "SELECT count(*) FROM rate_hits WHERE key = $1 AND ts >= clock_timestamp() - make_interval(secs => $2)", key, window)

    async def add(self, key: str) -> None:
        await self.db.execute("INSERT INTO rate_hits (key) VALUES ($1)", key)

    async def once_per_window(self, key: str, window: float) -> bool:
        """True for exactly one caller per window, across all replicas (used to write one audit row per burst)."""
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
                seen = await conn.fetchval(
                    "SELECT 1 FROM rate_hits WHERE key = $1 AND ts >= clock_timestamp() - make_interval(secs => $2) LIMIT 1", key, window)
                if seen:
                    return False
                await conn.execute("INSERT INTO rate_hits (key) VALUES ($1)", key)
                return True

    async def _sweep(self) -> None:
        """Every so often, drop anything old enough that no window could still need it."""
        self._calls += 1
        if self._calls % 500 == 0:
            await self.db.execute("DELETE FROM rate_hits WHERE ts < clock_timestamp() - interval '1 hour'")
