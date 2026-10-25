"""Upstream registry: keeps an in-memory view of every upstream's status and tools.

Why a cache: `tools/list` is answered from memory, so a slow or dead upstream can never block it.
A background loop re-checks every upstream independently (one failure does not affect another).
Tool calls open a short-lived MCP session to the one upstream they need, with hard timeouts.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
import asyncpg
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from .config import Settings
from .db import Database
from .security import CredentialBox, validate_upstream_url

log = logging.getLogger("gateway.registry")

CHANNEL = "urumi_servers"   # PostgreSQL notification: "the server list changed"
SEP = "__"   # slugs cannot contain "__", so the first "__" always splits slug from tool name
MAX_TOOL_NAME = 64


def public_name(slug: str, tool: str) -> str:
    return f"{slug}{SEP}{tool}"


def split_public_name(name: str) -> tuple[str, str] | None:
    slug, sep, tool = name.partition(SEP)
    return (slug, tool) if sep and slug and tool else None


@dataclass
class Upstream:
    id: str
    slug: str
    name: str
    url: str
    auth_type: str
    header_name: str | None
    credential: str | None
    enabled: bool
    status: str = "unknown"          # connected | unreachable | disabled | unknown
    detail: str | None = None
    tools: list[types.Tool] = field(default_factory=list)
    failures: int = 0


class UpstreamError(Exception):
    """A call to an upstream failed; the message is safe to show to the caller."""


class UpstreamTimeout(UpstreamError):
    pass


def describe(exc: BaseException) -> str:
    """Short, credential-free description of why an upstream call failed."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timed out"
    if isinstance(exc, httpx.ConnectError):
        return "connection refused or host unreachable"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return f"upstream rejected the gateway's credentials (HTTP {code})" if code in (401, 403) else f"HTTP {code}"
    if isinstance(exc, McpError):
        return f"MCP error: {str(exc.error.message)[:200]}"
    if isinstance(exc, ValueError):
        return str(exc)[:200]
    return type(exc).__name__


class Registry:
    def __init__(self, db: Database, box: CredentialBox, settings: Settings) -> None:
        self.db, self.box, self.cfg = db, box, settings
        self._by_slug: dict[str, Upstream] = {}
        self._task: asyncio.Task | None = None
        self._listener: asyncio.Task | None = None
        self._refresh_queued = False
        self._pending: set[str] = set()

    # ---- lifecycle -------------------------------------------------------------------------
    async def start(self) -> None:
        await self.refresh_all()
        self._task = asyncio.create_task(self._loop(), name="registry-refresh")
        self._listener = asyncio.create_task(self._listen(), name="registry-listen")

    async def stop(self) -> None:
        for task in (self._task, self._listener):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _listen(self) -> None:
        """With several gateway replicas, an admin's change lands on only one of them. That replica announces it through
        PostgreSQL, and every replica refreshes at once. The timed refresh stays as the fallback if this connection drops."""
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(self.db.dsn)
                await conn.add_listener(CHANNEL, self._heard)
                while not conn.is_closed():
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("change listener: %s, retrying", type(exc).__name__)
            finally:
                if conn is not None:
                    try:
                        await asyncio.wait_for(conn.close(), 2)
                    except Exception:  # noqa: BLE001
                        pass
            await asyncio.sleep(3)

    def _heard(self, *args) -> None:
        """A notice names the server that changed (asyncpg passes connection, pid, channel, payload)."""
        self._pending.add((args[3] if len(args) > 3 else "") or "*")
        if not self._refresh_queued:
            self._refresh_queued = True
            asyncio.ensure_future(self._refresh_soon())

    async def _refresh_soon(self) -> None:
        await asyncio.sleep(0.05)          # several notices at once are handled together
        self._refresh_queued = False
        pending, self._pending = self._pending, set()
        try:
            if "*" in pending:
                await self.refresh_all()
            else:
                for slug in sorted(pending):
                    await self.refresh_one(slug)    # exactly what the replica that made the change did
        except Exception:  # noqa: BLE001
            log.exception("refresh after a change notice failed")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.refresh_interval)
            try:
                await self.refresh_all()
            except Exception:  # never let the loop die
                log.exception("refresh loop error")

    # ---- reading ---------------------------------------------------------------------------
    def all(self) -> list[Upstream]:
        return list(self._by_slug.values())

    def get(self, slug: str) -> Upstream | None:
        return self._by_slug.get(slug)

    def listed_tools(self) -> list[tuple[Upstream, types.Tool]]:
        """Tools exposed to clients: only enabled, connected upstreams."""
        out = []
        for up in self._by_slug.values():
            if up.enabled and up.status == "connected":
                out.extend((up, t) for t in up.tools if len(public_name(up.slug, t.name)) <= MAX_TOOL_NAME)
        return out

    # ---- refreshing ------------------------------------------------------------------------
    async def _load_rows(self) -> list[Upstream]:
        rows = await self.db.fetch("SELECT * FROM mcp_servers ORDER BY created_at")
        result = []
        for r in rows:
            cred = None
            if r["credential_enc"]:
                try:
                    cred = self.box.decrypt(r["credential_enc"])
                except ValueError:
                    log.error("cannot decrypt credential for %s", r["slug"])
            result.append(Upstream(str(r["id"]), r["slug"], r["name"], r["url"], r["auth_type"],
                                   r["auth_header_name"], cred, r["enabled"]))
        return result

    async def refresh_all(self) -> None:
        fresh = await self._load_rows()
        previous = self._by_slug
        merged: dict[str, Upstream] = {}
        for up in fresh:
            old = previous.get(up.slug)
            if old and old.url == up.url and old.auth_type == up.auth_type and old.header_name == up.header_name:
                # Keep the SAME entry and update it in place. Two refreshes running at once (a timed one and a change
                # notice) then add to one failure count, instead of each copying the old count and one being lost.
                old.name, old.credential, old.enabled = up.name, up.credential, up.enabled
                up = old
            merged[up.slug] = up
        self._by_slug = merged
        await asyncio.gather(*(self._check(up) for up in merged.values()), return_exceptions=True)

    async def refresh_one(self, slug: str) -> None:
        up = next((u for u in await self._load_rows() if u.slug == slug), None)
        if up is None:
            self._by_slug.pop(slug, None)
            return
        # Check first, swap afterwards: until the new answer is in, clients keep seeing the old entry instead of a gap.
        await self._check(up)
        self._by_slug[slug] = up

    def forget(self, slug: str) -> None:
        self._by_slug.pop(slug, None)

    async def _check(self, up: Upstream) -> None:
        if not up.enabled:
            up.status, up.detail, up.tools, up.failures = "disabled", None, [], 0
        else:
            try:
                with anyio.fail_after(self.cfg.refresh_timeout):
                    tools = await self._list_tools(up)
                up.tools, up.status, up.detail, up.failures = tools, "connected", None, 0
            except BaseException as exc:  # noqa: BLE001 - includes ExceptionGroup from task groups
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                up.failures += 1
                up.detail = describe(exc)
                if up.failures >= self.cfg.fail_threshold:
                    up.status, up.tools = "unreachable", []
                    log.warning("upstream %s unreachable: %s", up.slug, up.detail)
                elif up.status == "unknown":
                    up.status = "unreachable"
        try:
            await self.db.execute(
                "UPDATE mcp_servers SET status=$2, status_detail=$3, last_checked_at=now(), tool_count=$4 WHERE id=$1",
                up.id, up.status, up.detail, len(up.tools))
        except Exception:
            log.exception("could not store status for %s", up.slug)

    # ---- talking to an upstream ------------------------------------------------------------
    def _headers(self, up: Upstream) -> dict[str, str]:
        # The gateway's own stored credential. The client's gateway key is never forwarded.
        if up.credential and up.auth_type == "bearer":
            return {"Authorization": f"Bearer {up.credential}"}
        if up.credential and up.auth_type == "header" and up.header_name:
            return {up.header_name: up.credential}
        return {}

    @asynccontextmanager
    async def _session(self, up: Upstream):
        await validate_upstream_url(up.url, self.cfg.allow_loopback_upstreams)   # re-check at connect time
        async with streamablehttp_client(
            up.url, headers=self._headers(up),
            timeout=self.cfg.connect_timeout, sse_read_timeout=self.cfg.call_timeout,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def _list_tools(self, up: Upstream) -> list[types.Tool]:
        tools: list[types.Tool] = []
        async with self._session(up) as session:
            cursor = None
            for _ in range(20):  # page cap
                page = await session.list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
        return tools

    async def call(self, up: Upstream, tool: str, arguments: dict | None) -> types.CallToolResult:
        try:
            with anyio.fail_after(self.cfg.call_timeout):
                async with self._session(up) as session:
                    return await session.call_tool(tool, arguments or {})
        except TimeoutError as exc:
            raise UpstreamTimeout(f"'{up.slug}' did not answer within {self.cfg.call_timeout:g}s") from exc
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            raise UpstreamError(f"'{up.slug}' call failed: {describe(exc)}") from exc
