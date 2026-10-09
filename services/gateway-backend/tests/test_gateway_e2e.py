"""End-to-end gateway test with a real Postgres, real MCP upstreams and a real MCP client.

Run (from services/gateway-backend):  pip install pgserver && python -m tests.test_gateway_e2e
"""
import asyncio
import json
import os
import socket
import tempfile
import time

import httpx
import pgserver
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.security import generate_api_key, validate_upstream_url

NOSEC = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RequireBearer:
    """ASGI wrapper that makes a fake upstream demand a bearer token."""
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if h.get("authorization") != f"Bearer {self.token}":
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)


def make_upstream(name: str, port: int, token: str | None = None):
    fm = FastMCP(name, host="127.0.0.1", port=port, stateless_http=True, json_response=True, transport_security=NOSEC)

    @fm.tool()
    def echo(text: str) -> str:
        """Echo text back, tagged with the upstream's name."""
        return f"{name}:{text}"

    @fm.tool()
    async def slow(seconds: float) -> str:
        """Sleep, then answer."""
        await asyncio.sleep(seconds)
        return f"{name}:done"

    @fm.tool()
    def whoami(ctx: Context) -> str:
        """Report the Authorization header this upstream received."""
        return ctx.request_context.request.headers.get("authorization", "none")

    app = fm.streamable_http_app()
    return RequireBearer(app, token) if token else app


async def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server, task


async def stop(server, task):
    server.should_exit = True
    await task


async def hang_server(port):
    """Accepts TCP connections and never answers: a worst-case 'slow' upstream."""
    async def handler(reader, writer):
        await asyncio.sleep(3600)
    return await asyncio.start_server(handler, "127.0.0.1", port)


def texts(res) -> str:
    return " ".join(getattr(c, "text", "") for c in res.content)


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


async def main():
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, admin_prefix, _ = generate_api_key()
    enc_key = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()

    ports = {n: free_port() for n in ("alpha", "beta", "gw", "dead", "hang")}
    alpha_srv = await serve(make_upstream("alpha", ports["alpha"], token="alpha-secret"), ports["alpha"])
    beta_srv = await serve(make_upstream("beta", ports["beta"]), ports["beta"])
    hang = await hang_server(ports["hang"])

    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": enc_key, "BOOTSTRAP_ADMIN_KEY": admin_key,
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "UPSTREAM_ALLOW_LOOPBACK": "true",
        "CALL_TIMEOUT_SECONDS": "2", "CONNECT_TIMEOUT_SECONDS": "2", "REFRESH_TIMEOUT_SECONDS": "2",
        "REFRESH_INTERVAL_SECONDS": "1", "FAIL_THRESHOLD": "2",
        "SEED_SERVERS": json.dumps([{"slug": "beta", "name": "Beta", "url": f"http://127.0.0.1:{ports['beta']}/mcp"}]),
    })
    from app.config import Settings
    from app.main import create_asgi
    gw_srv = await serve(create_asgi(Settings()), ports["gw"])
    base = f"http://127.0.0.1:{ports['gw']}"
    mcp_url = f"{base}/mcp"
    http = httpx.AsyncClient(base_url=base, timeout=20)
    A = {"Authorization": f"Bearer {admin_key}"}

    async def mcp(key, fn):
        async with streamablehttp_client(mcp_url, headers={"Authorization": f"Bearer {key}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)

    async def names(key):
        return sorted(t.name for t in (await mcp(key, lambda s: s.list_tools())).tools)

    # ---- auth --------------------------------------------------------------------------
    check("no key -> 401", (await http.post("/mcp", json={})).status_code == 401)
    check("garbage key -> 401", (await http.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})).status_code == 401)
    check("API needs a key", (await http.get("/api/servers")).status_code == 401)
    check("healthz/readyz", (await http.get("/healthz")).text == "ok" and (await http.get("/readyz")).text == "ok")

    # ---- seeded server is already registered ----------------------------------------------
    servers = (await http.get("/api/servers", headers=A)).json()
    check("seeded server registered and connected", [(s["slug"], s["status"]) for s in servers] == [("beta", "connected")])

    # ---- add an upstream that needs a credential ---------------------------------------------
    r = await http.post("/api/servers", headers=A, json={
        "slug": "alpha", "name": "Alpha", "url": f"http://127.0.0.1:{ports['alpha']}/mcp",
        "auth_type": "bearer", "credential": "alpha-secret"})
    check("create upstream with credential", r.status_code == 201 and r.json()["status"] == "connected", r.text[:120])
    check("credential never returned by API", "alpha-secret" not in r.text and r.json()["has_credential"] is True)
    check("duplicate slug -> 409", (await http.post("/api/servers", headers=A, json={
        "slug": "alpha", "name": "x", "url": "http://127.0.0.1:1/mcp"})).status_code == 409)
    check("bad slug (underscore) rejected", (await http.post("/api/servers", headers=A, json={
        "slug": "bad__slug", "name": "x", "url": "http://127.0.0.1:1/mcp"})).status_code == 422)
    import asyncpg
    conn = await asyncpg.connect(pg.get_uri())
    stored = await conn.fetchval("SELECT credential_enc FROM mcp_servers WHERE slug='alpha'")
    await conn.close()
    check("credential encrypted at rest", stored and "alpha-secret" not in stored, stored[:20] + "...")

    # ---- aggregation + collision handling ----------------------------------------------------
    got = await names(admin_key)
    check("aggregated, prefixed, no collision", got == sorted(f"{s}__{t}" for s in ("alpha", "beta") for t in ("echo", "slow", "whoami")), str(got))
    a = await mcp(admin_key, lambda s: s.call_tool("alpha__echo", {"text": "hi"}))
    b = await mcp(admin_key, lambda s: s.call_tool("beta__echo", {"text": "hi"}))
    check("routing: same tool name, right server", texts(a) == "alpha:hi" and texts(b) == "beta:hi", f"{texts(a)} / {texts(b)}")

    # ---- credential handling -------------------------------------------------------------
    who = texts(await mcp(admin_key, lambda s: s.call_tool("alpha__whoami", {})))
    check("upstream got the gateway's stored credential, not the client's key", who == "Bearer alpha-secret" and "urm_" not in who, who)

    # ---- members, keys, revocation ---------------------------------------------------------
    r = await http.post("/api/users", headers=A, json={"email": "bob@example.com", "name": "Bob", "role": "member"})
    check("invite member returns a key once", r.status_code == 201 and r.json()["key"]["api_key"].startswith("urm_"))
    bob_key, bob_key_id = r.json()["key"]["api_key"], r.json()["key"]["id"]
    B = {"Authorization": f"Bearer {bob_key}"}
    check("member can use tools", len(await names(bob_key)) == 6)
    codes = [(await http.get(p, headers=B)).status_code for p in ("/api/servers", "/api/users", "/api/audit")]
    check("member cannot manage servers/users/audit", codes == [403, 403, 403], str(codes))
    check("member can list own keys", (await http.get("/api/me/keys", headers=B)).status_code == 200)
    conn = await asyncpg.connect(pg.get_uri())
    row = await conn.fetchrow("SELECT key_hash FROM api_keys WHERE id=$1::uuid", bob_key_id)
    await conn.close()
    check("API key stored hashed", bob_key not in row["key_hash"] and len(row["key_hash"]) == 64)
    await mcp(bob_key, lambda s: s.call_tool("beta__echo", {"text": "from bob"}))
    check("revoke key", (await http.delete(f"/api/keys/{bob_key_id}", headers=A)).status_code == 204)
    check("revoked key rejected on next call", (await http.post("/mcp", json={}, headers=B)).status_code == 401)
    r2 = await http.post(f"/api/users/{r.json()['user']['id']}/keys", headers=A, json={"name": "new"})
    bob_key2 = r2.json()["api_key"]

    # ---- disable a server -------------------------------------------------------------------
    alpha_id = next(s["id"] for s in (await http.get("/api/servers", headers=A)).json() if s["slug"] == "alpha")
    r = await http.patch(f"/api/servers/{alpha_id}", headers=A, json={"enabled": False})
    check("disable server", r.json()["status"] == "disabled")
    check("disabled server's tools vanish", await names(bob_key2) == ["beta__echo", "beta__slow", "beta__whoami"])
    res = await mcp(bob_key2, lambda s: s.call_tool("alpha__echo", {"text": "x"}))
    check("calling a disabled server's tool is rejected", res.isError and "Unknown tool" in texts(res))
    await http.patch(f"/api/servers/{alpha_id}", headers=A, json={"enabled": True})
    check("re-enable brings tools back", len(await names(bob_key2)) == 6)

    # ---- timeouts ------------------------------------------------------------------------
    t0 = time.monotonic()
    res = await mcp(bob_key2, lambda s: s.call_tool("alpha__slow", {"seconds": 6}))
    took = time.monotonic() - t0
    check("slow tool times out with a clear error", res.isError and "Timeout" in texts(res) and took < 5, f"{took:.1f}s: {texts(res)}")
    res = await mcp(bob_key2, lambda s: s.call_tool("beta__echo", {"text": "still ok"}))
    check("other servers unaffected by the timeout", texts(res) == "beta:still ok")

    # ---- hanging and dead upstreams don't break listing ----------------------------------------
    t0 = time.monotonic()
    r = await http.post("/api/servers", headers=A, json={"slug": "hang", "name": "Hang", "url": f"http://127.0.0.1:{ports['hang']}/mcp"})
    check("hanging upstream registers as unreachable (bounded wait)", r.json()["status"] == "unreachable" and time.monotonic() - t0 < 6, r.json()["status_detail"])
    r = await http.post("/api/servers", headers=A, json={"slug": "dead", "name": "Dead", "url": f"http://127.0.0.1:{ports['dead']}/mcp"})
    check("dead upstream registers as unreachable", r.json()["status"] == "unreachable", r.json()["status_detail"])
    t0 = time.monotonic()
    got = await names(bob_key2)
    check("tool listing stays fast and complete despite bad upstreams", len(got) == 6 and time.monotonic() - t0 < 1.5, f"{time.monotonic() - t0:.2f}s")

    # ---- an upstream dies while running ----------------------------------------------------------
    await stop(*alpha_srv)
    await asyncio.sleep(4)   # two refresh intervals
    st = {s["slug"]: s["status"] for s in (await http.get("/api/servers", headers=A)).json()}
    check("killed upstream shown as unreachable, others connected", st["alpha"] == "unreachable" and st["beta"] == "connected", str(st))
    check("its tools disappear, others remain", await names(bob_key2) == ["beta__echo", "beta__slow", "beta__whoami"])
    res = await mcp(bob_key2, lambda s: s.call_tool("alpha__echo", {"text": "x"}))
    check("calling it gives a clear message", res.isError and "unreachable" in texts(res), texts(res)[:100])
    res = await mcp(bob_key2, lambda s: s.call_tool("beta__echo", {"text": "alive"}))
    check("other servers keep working", texts(res) == "beta:alive")

    # ---- edit + remove ----------------------------------------------------------------------
    hang_id = next(s["id"] for s in (await http.get("/api/servers", headers=A)).json() if s["slug"] == "hang")
    check("edit server name", (await http.patch(f"/api/servers/{hang_id}", headers=A, json={"name": "Renamed"})).json()["name"] == "Renamed")
    check("remove server", (await http.delete(f"/api/servers/{hang_id}", headers=A)).status_code == 204)

    # ---- SSRF guard ------------------------------------------------------------------------
    for url in ("http://169.254.169.254/latest/meta-data", "ftp://example.com/x", "http://user:pw@127.0.0.1/mcp"):
        r = await http.post("/api/servers", headers=A, json={"slug": "evil", "name": "Evil", "url": url})
        check(f"SSRF guard blocks {url}", r.status_code == 400, r.json().get("detail", ""))
    try:
        await validate_upstream_url("http://127.0.0.1:8080/mcp", allow_loopback=False)
        check("loopback blocked by default", False)
    except ValueError:
        check("loopback blocked by default", True)

    # ---- audit log ---------------------------------------------------------------------------
    rows = (await http.get("/api/audit?limit=200", headers=A)).json()
    by = lambda **kw: [r for r in rows if all(r[k] == v for k, v in kw.items())]
    check("audit: admin call attributed", by(user_email="admin@example.com", requested_name="alpha__echo", outcome="success") != [])
    check("audit: member call attributed", by(user_email="bob@example.com", requested_name="beta__echo", outcome="success") != [])
    check("audit: timeout recorded with duration", any(r["outcome"] == "timeout" and r["duration_ms"] >= 1500 for r in rows))
    check("audit: unreachable + rejected recorded", by(outcome="unreachable") != [] and by(outcome="rejected") != [])
    mine = (await http.get("/api/me/audit", headers={"Authorization": f"Bearer {bob_key2}"})).json()
    check("members see only their own audit rows", mine and all(r["user_email"] == "bob@example.com" for r in mine))

    for s in (beta_srv, gw_srv):
        await stop(*s)
    hang.close()
    pg.cleanup()
    print("ALL GATEWAY TESTS PASSED")


asyncio.run(main())
