"""Tool permissions: per-role rules hide tools and reject direct calls, for MCP clients AND the chat.

Real Postgres, real MCP upstreams, a real MCP client, and a scripted stand-in for the AI service.
Run (from services/gateway-backend):  python -m tests.test_permissions
"""
import asyncio
import json
import os
import socket
import tempfile

import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.permissions import decide, valid_tool
from app.security import generate_api_key

NOSEC = TransportSecuritySettings(enable_dns_rebinding_protection=False)
CSRF = {"X-Requested-With": "urumi-dashboard"}
CALLS: list[str] = []          # every call that really reached an upstream server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_shop(port):
    fm = FastMCP("shop", host="127.0.0.1", port=port, stateless_http=True, json_response=True, transport_security=NOSEC)

    @fm.tool()
    def list_orders(period: str = "today") -> str:
        """List orders (a read)."""
        CALLS.append("shop.list_orders")
        return "orders:2"

    @fm.tool()
    def create_product(name: str, price: str) -> str:
        """Create a product (a write)."""
        CALLS.append("shop.create_product")
        return f"created:{name}"

    @fm.tool()
    def update_order_status(order_id: int, status: str) -> str:
        """Change an order's status (a write)."""
        CALLS.append("shop.update_order_status")
        return "updated"
    return fm.streamable_http_app()


def make_notes(port):
    fm = FastMCP("notes", host="127.0.0.1", port=port, stateless_http=True, json_response=True, transport_security=NOSEC)

    @fm.tool()
    def add_note(title: str, body: str) -> str:
        """Save a note (a write)."""
        CALLS.append("notes.add_note")
        return "saved"

    @fm.tool()
    def list_notes() -> str:
        """List notes (a read)."""
        CALLS.append("notes.list_notes")
        return "notes:1"
    return fm.streamable_http_app()


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeLLM:
    """Plays the AI service: asks for create_product when told to, otherwise reports what it got."""
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        msgs = payload["messages"]
        last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        results = [m["content"] for m in msgs[last_user:] if m["role"] == "tool"]

        def reply(content=None, tool_calls=None):
            msg = {"role": "assistant", "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return httpx.Response(200, json={"choices": [{"message": msg}]})
        if results:
            return reply("RESULT: " + " | ".join(results))
        if "create" in msgs[last_user]["content"]:
            return reply(tool_calls=[call("shop__create_product", {"name": "Mug", "price": "299"})])
        return reply("no tool needed")


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def texts(res) -> str:
    return " ".join(getattr(c, "text", "") for c in res.content)


async def main():
    # ---- unit: which rule wins -------------------------------------------------------------------
    rules = {("shop", "*"): False, ("shop", "list_orders"): True}
    check("unit: no rule -> allowed by default", decide({}, "shop", "x") == (True, "default"))
    check("unit: server-wide rule applies to its tools", decide(rules, "shop", "create_product") == (False, "server"))
    check("unit: a tool rule beats the server-wide rule", decide(rules, "shop", "list_orders") == (True, "tool"))
    check("unit: rules do not leak to other servers", decide(rules, "notes", "add_note") == (True, "default"))
    check("unit: tool-name validation", valid_tool("*") and valid_tool("create_product") and not valid_tool("bad name!") and not valid_tool("") and not valid_tool("x" * 65))

    ports = {n: free_port() for n in ("shop", "notes", "gw")}
    servers = []
    for name, app in (("shop", make_shop(ports["shop"])), ("notes", make_notes(ports["notes"]))):
        s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports[name], log_level="warning"))
        servers.append((s, asyncio.create_task(s.serve())))
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(), "BOOTSTRAP_ADMIN_KEY": admin_key,
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "BOOTSTRAP_ADMIN_PASSWORD": "correct-horse-battery",
        "UPSTREAM_ALLOW_LOOPBACK": "true", "CALL_TIMEOUT_SECONDS": "3", "REFRESH_INTERVAL_SECONDS": "60", "REFRESH_TIMEOUT_SECONDS": "5",
        "LLM_API_KEY": "test-llm-key", "LLM_MODEL": "test-model", "CHAT_MAX_STEPS": "3", "CHAT_RATE_PER_MINUTE": "50",
        "SEED_SERVERS": json.dumps([{"slug": "shop", "name": "Shop", "url": f"http://127.0.0.1:{ports['shop']}/mcp"},
                                    {"slug": "notes", "name": "Notes", "url": f"http://127.0.0.1:{ports['notes']}/mcp"}]),
    })
    from app.config import Settings
    from app.main import create_asgi
    fake = FakeLLM()
    gw = uvicorn.Server(uvicorn.Config(create_asgi(Settings(), llm_transport=httpx.MockTransport(fake.handler)),
                                       host="127.0.0.1", port=ports["gw"], log_level="warning"))
    gw_task = asyncio.create_task(gw.serve())
    while not (gw.started and all(s.started for s, _ in servers)):
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{ports['gw']}"
    A = {"Authorization": f"Bearer {admin_key}"}
    http = httpx.AsyncClient(base_url=base, timeout=30)
    await asyncio.sleep(1.5)     # the seeded servers finish their first check

    async def mcp(key, fn):
        async with streamablehttp_client(f"{base}/mcp", headers={"Authorization": f"Bearer {key}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)

    async def names(key):
        return sorted(t.name for t in (await mcp(key, lambda s: s.list_tools())).tools)

    async def put(changes, headers=A):
        return await http.put("/api/permissions", json={"changes": changes}, headers=headers)

    def cell(matrix, server, tool, role):
        srv = next(s for s in matrix["servers"] if s["slug"] == server)
        return next(t for t in srv["tools"] if t["name"] == tool)["access"][role]

    ALL = sorted(["shop__list_orders", "shop__create_product", "shop__update_order_status", "notes__add_note", "notes__list_notes"])
    inv = (await http.post("/api/users", headers=A, json={"email": "bob@example.com", "name": "Bob", "role": "member"})).json()
    bob_key = inv["key"]["api_key"]
    B = {"Authorization": f"Bearer {bob_key}"}
    bob = httpx.AsyncClient(base_url=base, timeout=30)
    await bob.post("/api/auth/login", json={"email": "bob@example.com", "password": inv["temporary_password"]})
    adm = httpx.AsyncClient(base_url=base, timeout=30)
    await adm.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})

    # ---- default: nothing is restricted ------------------------------------------------------------
    check("default: a member sees every tool", await names(bob_key) == ALL)
    m = (await http.get("/api/permissions", headers=A)).json()
    check("default: matrix shows everything allowed by default", m["roles"] == ["admin", "member"] and cell(m, "shop", "create_product", "member") == {"allowed": True, "source": "default"})
    check("default: a member's call works", "created:Mug" in texts(await mcp(bob_key, lambda s: s.call_tool("shop__create_product", {"name": "Mug", "price": "1"}))))

    # ---- only admins may read or change the rules -----------------------------------------------------
    check("member cannot read the rules (403)", (await http.get("/api/permissions", headers=B)).status_code == 403)
    check("member cannot change the rules (403)", (await put([{"role": "member", "server": "shop", "tool": "*", "allow": False}], headers=B)).status_code == 403)
    check("no key -> 401", (await http.get("/api/permissions")).status_code == 401)
    r = await adm.put("/api/permissions", json={"changes": [{"role": "member", "server": "shop", "tool": "*", "allow": False}]})
    check("a signed-in admin needs the CSRF header too (403 without)", r.status_code == 403)
    check("rules were NOT changed by that rejected request", await names(bob_key) == ALL)

    # ---- block the two write tools of the shop for members -----------------------------------------------
    CALLS.clear()
    r = await adm.put("/api/permissions", headers=CSRF, json={"changes": [
        {"role": "member", "server": "shop", "tool": "create_product", "allow": False},
        {"role": "member", "server": "shop", "tool": "update_order_status", "allow": False}]})
    check("admin saves the rules (with the CSRF header)", r.status_code == 200)
    m = r.json()
    check("matrix: blocked for member, still allowed for admin",
          cell(m, "shop", "create_product", "member") == {"allowed": False, "source": "tool"} and cell(m, "shop", "create_product", "admin")["allowed"] is True
          and cell(m, "shop", "list_orders", "member")["allowed"] is True)
    check("member's tool list hides them at once", await names(bob_key) == sorted(set(ALL) - {"shop__create_product", "shop__update_order_status"}))
    check("admin still sees everything", await names(admin_key) == ALL)
    blocked = await mcp(bob_key, lambda s: s.call_tool("shop__create_product", {"name": "Hack", "price": "0"}))
    check("a direct call to a blocked tool is rejected", blocked.isError and "not permitted" in texts(blocked), texts(blocked)[:90])
    blocked2 = await mcp(bob_key, lambda s: s.call_tool("shop__update_order_status", {"order_id": 1, "status": "completed"}))
    check("the other blocked tool is rejected too", blocked2.isError and "not permitted" in texts(blocked2))
    check("the upstream was never contacted for either", "shop.create_product" not in CALLS and "shop.update_order_status" not in CALLS, str(CALLS))
    ok = await mcp(bob_key, lambda s: s.call_tool("shop__list_orders", {}))
    check("reading orders still works for the member", not ok.isError and "orders:2" in texts(ok))
    ok = await mcp(bob_key, lambda s: s.call_tool("notes__add_note", {"title": "t", "body": "b"}))
    check("other servers are unaffected (notes)", not ok.isError and "saved" in texts(ok))
    ad = await mcp(admin_key, lambda s: s.call_tool("shop__create_product", {"name": "Real", "price": "5"}))
    check("an admin can still create products", not ad.isError and "created:Real" in texts(ad) and "shop.create_product" in CALLS)
    rows = (await http.get("/api/audit?outcome=rejected&limit=50", headers=A)).json()
    mine = [x for x in rows if x["requested_name"] == "shop__create_product" and x["user_email"] == "bob@example.com"]
    check("audit: the rejected call is recorded under the member, with the reason", mine and "not permitted" in (mine[0]["detail"] or "") and mine[0]["source"] == "mcp")
    tools_api = [t["name"] for t in (await http.get("/api/tools", headers=B)).json()]
    check("/api/tools is filtered for the member too", "shop__create_product" not in tools_api and "shop__list_orders" in tools_api)

    # ---- the chat sees the same rules ----------------------------------------------------------------------
    r = await bob.post("/api/chat", json={"messages": [{"role": "user", "content": "please create a mug"}]}, headers=CSRF)
    out = r.json()
    sent = sorted(t["function"]["name"] for t in fake.requests[0]["tools"])
    check("chat: the AI is never offered a blocked tool", "shop__create_product" not in sent and "shop__list_orders" in sent, str(sent))
    check("chat: if the AI asks for it anyway, it is rejected and shown as such", out["tool_calls"][0]["name"] == "shop__create_product" and out["tool_calls"][0]["outcome"] == "rejected", str(out["tool_calls"]))
    check("chat: the reply passes the refusal on", "not permitted" in out["reply"])
    check("chat: still nothing reached the upstream", CALLS.count("shop.create_product") == 1)   # only the admin's call above
    r = await adm.post("/api/chat", json={"messages": [{"role": "user", "content": "please create a mug"}]}, headers=CSRF)
    check("chat: an admin may use it", r.json()["tool_calls"][0]["outcome"] == "success")

    # ---- a server-wide block with an exception for one tool -----------------------------------------------------
    r = await put([{"role": "member", "server": "notes", "tool": "*", "allow": False}, {"role": "member", "server": "notes", "tool": "list_notes", "allow": True}])
    m = r.json()
    check("server-wide block + one exception: matrix", cell(m, "notes", "add_note", "member") == {"allowed": False, "source": "server"} and cell(m, "notes", "list_notes", "member") == {"allowed": True, "source": "tool"})
    got = await names(bob_key)
    check("member sees only the excepted notes tool", "notes__list_notes" in got and "notes__add_note" not in got)
    res = await mcp(bob_key, lambda s: s.call_tool("notes__add_note", {"title": "t", "body": "b"}))
    check("the blocked notes tool is rejected", res.isError and "not permitted" in texts(res))

    # ---- admins can be restricted too ---------------------------------------------------------------------
    await put([{"role": "admin", "server": "shop", "tool": "update_order_status", "allow": False}])
    check("rules work for the admin role as well", "shop__update_order_status" not in await names(admin_key))
    check("the admin can still manage rules (not a tool)", (await http.get("/api/permissions", headers=A)).status_code == 200)

    # ---- removing a rule puts things back ---------------------------------------------------------------------
    r = await put([{"role": "admin", "server": "shop", "tool": "update_order_status", "allow": None},
                   {"role": "member", "server": "notes", "tool": "*", "allow": None}, {"role": "member", "server": "notes", "tool": "list_notes", "allow": None},
                   {"role": "member", "server": "shop", "tool": "create_product", "allow": None}, {"role": "member", "server": "shop", "tool": "update_order_status", "allow": None}])
    check("removing the rules restores every tool", r.status_code == 200 and await names(bob_key) == ALL and await names(admin_key) == ALL)
    n = await pg_count(pg)
    check("no rules left in the database", n == 0, str(n))

    # ---- validation ----------------------------------------------------------------------------------------------
    good = {"role": "member", "server": "shop", "tool": "*", "allow": False}
    check("unknown role -> 422", (await put([{**good, "role": "owner"}])).status_code == 422)
    check("unknown server -> 404", (await put([{**good, "server": "nope"}])).status_code == 404)
    check("bad tool name -> 400", (await put([{**good, "tool": "bad name!"}])).status_code == 400)
    check("bad server name -> 422", (await put([{**good, "server": "Bad__Slug"}])).status_code == 422)
    check("empty change list -> 422", (await put([])).status_code == 422)
    check("too many changes -> 422", (await put([good] * 201)).status_code == 422)
    check("a failed request changed nothing", await pg_count(pg) == 0 and await names(bob_key) == ALL)
    r = await put([{**good, "tool": "create_product"}, {**good, "server": "nope"}])
    check("a bad entry cancels the whole request", r.status_code == 404 and await pg_count(pg) == 0)

    # ---- removing a server removes its rules ----------------------------------------------------------------------
    r = await http.post("/api/servers", headers=A, json={"slug": "tmp", "name": "Temp", "url": f"http://127.0.0.1:{ports['notes']}/mcp"})
    sid = r.json()["id"]
    await put([{"role": "member", "server": "tmp", "tool": "*", "allow": False}])
    check("rule exists for the new server", await pg_count(pg) == 1)
    await http.delete(f"/api/servers/{sid}", headers=A)
    check("deleting the server deleted its rules", await pg_count(pg) == 0)
    await http.post("/api/servers", headers=A, json={"slug": "tmp", "name": "Temp", "url": f"http://127.0.0.1:{ports['notes']}/mcp"})
    m = (await http.get("/api/permissions", headers=A)).json()
    check("a server re-added under the same name starts with no rules", next(s for s in m["servers"] if s["slug"] == "tmp")["server_rules"] == {"admin": None, "member": None})

    print("ALL PERMISSION TESTS PASSED")
    gw.should_exit = True
    await gw_task
    for s, t in servers:
        s.should_exit = True
        await t


async def pg_count(pg) -> int:
    import asyncpg
    conn = await asyncpg.connect(pg.get_uri())
    try:
        return await conn.fetchval("SELECT count(*) FROM tool_rules")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
