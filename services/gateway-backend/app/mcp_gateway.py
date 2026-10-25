"""The gateway's own MCP endpoint (Streamable HTTP, stateless).

Clients see one flat tool list. Tool names are "<server-slug>__<tool>", so two upstreams that both
expose "search" never collide, and the gateway can route a call from the name alone. The work itself
is done by ToolService, which the dashboard chat agent shares.
"""
from __future__ import annotations

import inspect
import json

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from .auth import AuthUser, authenticate, bearer_token
from .compact import META_TOOLS, RUN, SCHEMA, SEARCH, Compact
from .db import Database
from .ratelimit import RateLimiter
from .toolservice import ToolService


def build_mcp(tools: ToolService) -> tuple[Server, StreamableHTTPSessionManager]:
    server = Server(
        "urumi-gateway",
        instructions=("One endpoint for every MCP server your team has connected. "
                      "Tool names look like <server>__<tool>."),
    )

    def caller() -> AuthUser:
        return server.request_context.request.state.user  # set by McpAuth after the key is verified

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await tools.list_tools(caller())

    @server.call_tool(validate_input=False)   # the upstream validates its own arguments
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return (await tools.call(caller(), name, arguments, source="mcp")).result

    manager = StreamableHTTPSessionManager(
        app=server, json_response=True, stateless=True,
        # Reached through cluster DNS and the ingress, under varying Host headers. Access control is
        # the API key plus network policy, not Host-header filtering (that guards browsers on localhost).
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server, manager


async def _resolve(value):
    """A limiter may answer at once (in memory) or be awaited (shared in PostgreSQL)."""
    return await value if inspect.isawaitable(value) else value


def build_compact_mcp(compact: Compact) -> tuple[Server, StreamableHTTPSessionManager]:
    """The same gateway seen through three tools (search, schema, run) instead of the whole catalogue: /mcp/compact."""
    server = Server(
        "urumi-gateway-compact",
        instructions=("A large catalogue behind three tools. Use search_tools to find the tool you need, get_tool_schema to see its inputs, "
                      "and run_tool to run it by its exact name."),
    )

    def caller() -> AuthUser:
        return server.request_context.request.state.user

    def text(obj) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(obj, separators=(",", ":")))])

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return META_TOOLS

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        arguments = arguments or {}
        if name == SEARCH:
            return text(await compact.search(caller(), arguments.get("query"), arguments.get("server"), arguments.get("limit", 8)))
        if name == SCHEMA:
            found = await compact.schema(caller(), arguments.get("name"))
            if found is None:
                return types.CallToolResult(content=[types.TextContent(type="text", text="No such tool is available to you. Use search_tools to find the exact name.")], isError=True)
            return text(found)
        if name == RUN:
            return (await compact.run(caller(), arguments.get("name"), arguments.get("arguments"), source="mcp")).result
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"Unknown tool '{name}'. This endpoint has: {SEARCH}, {SCHEMA}, {RUN}.")], isError=True)

    manager = StreamableHTTPSessionManager(
        app=server, json_response=True, stateless=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server, manager


class McpAuth:
    """ASGI wrapper: verify the API key, then hand the request to the MCP session manager."""

    def __init__(self, manager: StreamableHTTPSessionManager, db: Database, limiter: RateLimiter | None = None,
                 tools: ToolService | None = None) -> None:
        self.manager, self.db, self.limiter, self.tools = manager, db, limiter, tools

    async def __call__(self, scope, receive, send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        user = await authenticate(self.db, bearer_token(headers.get("authorization")))
        if user is None:
            body = json.dumps({"error": "invalid, revoked or missing API key"}).encode()
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"content-type", b"application/json"), (b"www-authenticate", b'Bearer realm="urumi-gateway"'),
                (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        if self.limiter is not None and self.limiter.enabled:
            allowed, retry_after = await _resolve(self.limiter.check(user.key_id))
            if not allowed:
                if self.tools is not None:
                    await self.tools.record_rate_limited(user, self.limiter.per_minute, await _resolve(self.limiter.first_rejection(user.key_id)))
                body = json.dumps({"error": f"rate limit exceeded: at most {self.limiter.per_minute} requests per minute for this key",
                                   "retry_after_seconds": retry_after}).encode()
                await send({"type": "http.response.start", "status": 429, "headers": [
                    (b"content-type", b"application/json"), (b"retry-after", str(retry_after).encode()),
                    (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        scope.setdefault("state", {})["user"] = user
        await self.manager.handle_request(scope, receive, send)
