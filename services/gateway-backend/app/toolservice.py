"""Lists tools and runs tool calls. This is the ONE place that does it.

Both the MCP endpoint (external clients such as Claude Code) and the dashboard chat agent call this
class, so routing, timeouts, error messages and the audit log behave identically for both. Nothing
talks to an upstream server except through here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from mcp import types

from .auth import AuthUser
from .db import Database
from .permissions import Permissions, decide
from .registry import Registry, UpstreamError, UpstreamTimeout, public_name, split_public_name

log = logging.getLogger("gateway.tools")


@dataclass
class ToolRun:
    result: types.CallToolResult
    outcome: str            # success | error | timeout | unreachable | rejected
    detail: str | None
    duration_ms: int
    server: str | None
    tool: str | None


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)


class ToolService:
    def __init__(self, db: Database, registry: Registry, permissions: Permissions | None = None) -> None:
        self.db, self.registry = db, registry
        self.permissions = permissions or Permissions(db)

    async def visible(self, user: AuthUser) -> list:
        """(upstream, tool) pairs this person's role may see: connected, enabled and not blocked for their role."""
        rules = await self.permissions.rules_for(user.role)
        return [(up, t) for up, t in self.registry.listed_tools() if decide(rules, up.slug, t.name)[0]]

    async def list_tools(self, user: AuthUser) -> list[types.Tool]:
        out = []
        for up, tool in await self.visible(user):
            desc = f"[{up.name}] {tool.description or ''}".strip()[:2000]   # upstream text is untrusted
            out.append(tool.model_copy(update={"name": public_name(up.slug, tool.name), "description": desc}))
        return out

    async def call(self, user: AuthUser, name: str, arguments: dict | None, source: str = "mcp") -> ToolRun:
        started = time.monotonic()
        slug = tool = None
        outcome, detail = "success", None
        try:
            parts = split_public_name(name)
            up = self.registry.get(parts[0]) if parts else None
            if parts is None or up is None or not up.enabled:
                outcome, detail = "rejected", "unknown tool"
                result = _error(f"Unknown tool '{name}'.")
            else:
                slug, tool = parts
                if not await self.permissions.allowed(user.role, slug, tool):
                    # Hidden from the tool list too; this catches someone who calls a blocked tool by name.
                    outcome, detail = "rejected", f"not permitted for the {user.role} role"
                    result = _error(f"The tool '{name}' is not permitted for your role ({user.role}). Ask an admin if you need it.")
                elif up.status != "connected":
                    outcome, detail = "unreachable", up.detail
                    result = _error(f"Server '{slug}' is currently unreachable ({up.detail or 'no details'}). Other servers are unaffected.")
                elif not any(t.name == tool for t in up.tools):
                    outcome, detail = "rejected", "unknown tool"
                    result = _error(f"Unknown tool '{name}'.")
                else:
                    result = await self.registry.call(up, tool, arguments)
                    if result.isError:
                        outcome = "error"
                        detail = result.content[0].text[:300] if result.content and hasattr(result.content[0], "text") else None
        except UpstreamTimeout as exc:
            outcome, detail = "timeout", str(exc)
            result = _error(f"Timeout: {exc}.")
        except UpstreamError as exc:
            outcome, detail = "error", str(exc)
            result = _error(str(exc))
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            await self.db.execute(
                """INSERT INTO audit_log (user_id, user_email, key_prefix, server_slug, tool_name, requested_name,
                                          duration_ms, outcome, detail, source)
                   VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                user.id, user.email, user.key_prefix, slug, tool, name[:200], duration_ms, outcome, detail, source)
        except Exception:
            log.exception("failed to write audit log")
        return ToolRun(result, outcome, detail, duration_ms, slug, tool)
