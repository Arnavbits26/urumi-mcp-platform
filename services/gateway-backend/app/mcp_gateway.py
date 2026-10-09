"""The gateway's own MCP endpoint (Streamable HTTP, stateless).

Clients see one flat tool list. Tool names are "<server-slug>__<tool>", so two upstreams that both
expose "search" never collide, and the gateway can route a call from the name alone.
"""
from __future__ import annotations

import json
import logging
import time

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from .auth import AuthUser, authenticate, bearer_token
from .db import Database
from .registry import Registry, UpstreamError, UpstreamTimeout, public_name, split_public_name

log = logging.getLogger("gateway.mcp")


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)


def build_mcp(db: Database, registry: Registry) -> tuple[Server, StreamableHTTPSessionManager]:
    server = Server(
        "urumi-gateway",
        instructions=("One endpoint for every MCP server your team has connected. "
                      "Tool names look like <server>__<tool>."),
    )

    def caller() -> AuthUser:
        return server.request_context.request.state.user  # set by McpAuth after the key is verified

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        out = []
        for up, tool in registry.listed_tools():
            desc = f"[{up.name}] {tool.description or ''}".strip()[:2000]   # upstream text is untrusted
            out.append(tool.model_copy(update={"name": public_name(up.slug, tool.name), "description": desc}))
        return out

    @server.call_tool(validate_input=False)   # the upstream validates its own arguments
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        user = caller()
        started = time.monotonic()
        slug = tool = None
        outcome, detail = "success", None
        try:
            parts = split_public_name(name)
            up = registry.get(parts[0]) if parts else None
            if parts is None or up is None or not up.enabled:
                outcome, detail = "rejected", "unknown tool"
                return _error(f"Unknown tool '{name}'.")
            slug, tool = parts
            if up.status != "connected":
                outcome, detail = "unreachable", up.detail
                return _error(f"Server '{slug}' is currently unreachable ({up.detail or 'no details'}). Other servers are unaffected.")
            if not any(t.name == tool for t in up.tools):
                outcome, detail = "rejected", "unknown tool"
                return _error(f"Unknown tool '{name}'.")
            result = await registry.call(up, tool, arguments)
            if result.isError:
                outcome = "error"
                detail = (result.content[0].text[:300] if result.content and hasattr(result.content[0], "text") else None)
            return result
        except UpstreamTimeout as exc:
            outcome, detail = "timeout", str(exc)
            return _error(f"Timeout: {exc}.")
        except UpstreamError as exc:
            outcome, detail = "error", str(exc)
            return _error(str(exc))
        finally:
            try:
                await db.execute(
                    """INSERT INTO audit_log (user_id, user_email, key_prefix, server_slug, tool_name, requested_name,
                                              duration_ms, outcome, detail)
                       VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    user.id, user.email, user.key_prefix, slug, tool, name[:200],
                    int((time.monotonic() - started) * 1000), outcome, detail)
            except Exception:
                log.exception("failed to write audit log")

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
