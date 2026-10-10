"""Checks that the JSON the real backend returns has every field the dashboard reads.
Run: python -m tests.test_ui_contract"""
import asyncio
import os
import socket
import tempfile

import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.security import generate_api_key

# Field names the React code reads (src/pages/*.jsx). Keep in sync with the dashboard.
SERVER = {"id", "slug", "name", "url", "auth_type", "auth_header_name", "has_credential", "enabled", "status", "status_detail", "last_checked_at", "tools"}
TOOL = {"name", "public_name", "description"}
USER = {"id", "email", "name", "role", "disabled"}
KEY = {"id", "name", "prefix", "created_at", "last_used_at", "revoked_at"}
AUDIT = {"id", "ts", "user_email", "server", "tool", "requested_name", "duration_ms", "outcome", "detail", "source"}
ME = {"id", "email", "name", "role"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def has(label, got, needed):
    missing = needed - set(got)
    assert not missing, f"FAIL {label}: dashboard reads {sorted(missing)} but the API does not return it"
    print("PASS", label)


async def main():
    up_port = free_port()
    fm = FastMCP("contract", host="127.0.0.1", port=up_port, stateless_http=True, json_response=True,
                 transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @fm.tool()
    def ping() -> str:
        """Reply pong."""
        return "pong"

    up = uvicorn.Server(uvicorn.Config(fm.streamable_http_app(), host="127.0.0.1", port=up_port, log_level="warning"))
    up_task = asyncio.create_task(up.serve())
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({"DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(),
                       "BOOTSTRAP_ADMIN_KEY": admin_key, "UPSTREAM_ALLOW_LOOPBACK": "true", "REFRESH_INTERVAL_SECONDS": "60"})
    from app.config import Settings
    from app.main import create_asgi
    gw_port = free_port()
    gw = uvicorn.Server(uvicorn.Config(create_asgi(Settings()), host="127.0.0.1", port=gw_port, log_level="warning"))
    gw_task = asyncio.create_task(gw.serve())
    while not (up.started and gw.started):
        await asyncio.sleep(0.05)

    c = httpx.AsyncClient(base_url=f"http://127.0.0.1:{gw_port}", headers={"Authorization": f"Bearer {admin_key}"}, timeout=20)
    r = await c.post("/api/servers", json={"slug": "demo", "name": "Demo", "url": f"http://127.0.0.1:{up_port}/mcp"})
    has("create server", r.json(), SERVER)
    servers = (await c.get("/api/servers")).json()
    has("list servers", servers[0], SERVER)
    has("server tools", servers[0]["tools"][0], TOOL)
    has("me", (await c.get("/api/me")).json(), ME)
    inv = (await c.post("/api/users", json={"email": "bob@example.com", "name": "Bob", "role": "member"})).json()
    has("invite", inv, {"user", "key", "temporary_password"})
    has("invite user", inv["user"], USER)
    has("invite key", inv["key"], KEY | {"api_key"})
    has("list users", (await c.get("/api/users")).json()[0], USER)
    has("user keys", (await c.get(f"/api/users/{inv['user']['id']}/keys")).json()[0], KEY)
    has("my keys", (await c.get("/api/me/keys")).json()[0], KEY)
    has("issue key", (await c.post(f"/api/users/{inv['user']['id']}/keys", json={"name": "x"})).json(), KEY | {"api_key"})
    has("reset password", (await c.post(f"/api/users/{inv['user']['id']}/reset-password")).json(), {"temporary_password"})
    # make one audit row via the MCP endpoint
    await c.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                 json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "demo__ping", "arguments": {}}})
    has("audit", (await c.get("/api/audit")).json()[0], AUDIT)
    has("my audit", (await c.get("/api/me/audit")).json()[0], AUDIT)
    has("chat status", (await c.get("/api/chat/status")).json(), {"configured", "model"})
    login = await httpx.AsyncClient(base_url=f"http://127.0.0.1:{gw_port}").post("/api/auth/login", json={"email": "bob@example.com", "password": "x"})
    assert login.status_code == 401 and isinstance(login.json()["detail"], str), "login error must be a plain string"
    print("PASS login error shape")

    for s in (up, gw):
        s.should_exit = True
    await asyncio.gather(up_task, gw_task)
    pg.cleanup()
    print("UI CONTRACT OK")


asyncio.run(main())
