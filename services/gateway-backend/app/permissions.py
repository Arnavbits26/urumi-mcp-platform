"""Per-role rules for which tools may be listed and called.

The default is ALLOW, so an install with no rules behaves exactly as before. A rule names a role, a
server and a tool (or "*" for every tool of that server) and says allow or block. The most specific
rule wins: a tool rule beats a server-wide rule, which beats the default.

This is enforced in ToolService, the one place the MCP endpoint and the dashboard chat both use, so a
blocked tool is hidden from the tool list AND rejected if someone calls it directly.
"""
from __future__ import annotations

import re

from .db import Database

ROLES = ("admin", "member")
WILDCARD = "*"
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")   # tool names as upstream MCP servers define them

Rules = dict[tuple[str, str], bool]                   # (server slug, tool or "*") -> allowed


def decide(rules: Rules, server: str, tool: str) -> tuple[bool, str]:
    """Return (allowed, source) where source says what decided it: "tool", "server" or "default"."""
    if (server, tool) in rules:
        return rules[(server, tool)], "tool"
    if (server, WILDCARD) in rules:
        return rules[(server, WILDCARD)], "server"
    return True, "default"


def valid_tool(tool: str) -> bool:
    return tool == WILDCARD or bool(TOOL_NAME.match(tool))


class Permissions:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def rules_for(self, role: str) -> Rules:
        rows = await self.db.fetch("SELECT server_slug, tool_name, allow FROM tool_rules WHERE role = $1", role)
        return {(r["server_slug"], r["tool_name"]): r["allow"] for r in rows}

    async def all_rules(self) -> dict[str, Rules]:
        out: dict[str, Rules] = {role: {} for role in ROLES}
        for r in await self.db.fetch("SELECT role, server_slug, tool_name, allow FROM tool_rules"):
            out[r["role"]][(r["server_slug"], r["tool_name"])] = r["allow"]
        return out

    async def allowed(self, role: str, server: str, tool: str) -> bool:
        return decide(await self.rules_for(role), server, tool)[0]

    async def apply(self, changes: list[dict], who: str) -> None:
        """Apply several changes in one transaction. allow=None removes the rule (back to inheriting)."""
        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                for c in changes:
                    if c["allow"] is None:
                        await conn.execute("DELETE FROM tool_rules WHERE role=$1 AND server_slug=$2 AND tool_name=$3",
                                           c["role"], c["server"], c["tool"])
                    else:
                        await conn.execute(
                            """INSERT INTO tool_rules (role, server_slug, tool_name, allow, updated_by)
                               VALUES ($1,$2,$3,$4,$5)
                               ON CONFLICT (role, server_slug, tool_name)
                               DO UPDATE SET allow = EXCLUDED.allow, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                            c["role"], c["server"], c["tool"], c["allow"], who)

    async def forget_server(self, slug: str) -> None:
        await self.db.execute("DELETE FROM tool_rules WHERE server_slug = $1", slug)
