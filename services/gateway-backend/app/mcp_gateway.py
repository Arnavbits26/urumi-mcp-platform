"""The gateway's own MCP endpoint (Streamable HTTP, stateless).

Clients see one flat tool list. Tool names are "<server-slug>__<tool>", so two upstreams that both
expose "search" never collide, and the gateway can route a call from the name alone. The work itself
is done by ToolService, which the dashboard chat agent shares.
"""
from __future__ import annotations

import json

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from .auth import AuthUser, authenticate, bearer_token
from .db import Database
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


class McpAuth:
    """ASGI wrapper: verify the API key, then hand the request to the MCP session manager."""

    def __init__(self, manager: StreamableHTTPSessionManager, db: Database) -> None:
        self.manager, self.db = manager, db

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
        scope.setdefault("state", {})["user"] = user
        await self.manager.handle_request(scope, receive, send)
