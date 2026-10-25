"""Scaling: two gateway replicas on one database behave like one gateway.

Real Postgres, real MCP upstreams, a real MCP client, and a round-robin proxy that plays the part of a
Kubernetes Service in front of two replicas. Replicas A and B share their counters through PostgreSQL;
replicas C and D are the CONTROL: they use the old in-memory counters, to show what goes wrong without sharing.
Run (from services/gateway-backend):  python -m tests.test_scaling
"""
import asyncio
import json
import os
import socket
import tempfile
import time

import asyncpg
import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.security import generate_api_key

NOSEC = TransportSecuritySettings(enable_dns_rebinding_protection=False)
CSRF = {"X-Requested-With": "urumi-dashboard"}
MCP_H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
LIMIT, WINDOW = 10, 4


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_upstream(name, tool, port):
    fm = FastMCP(name, host="127.0.0.1", port=port, stateless_http=True, json_response=True, transport_security=NOSEC)

    @fm.tool(name=tool)
    def fn() -> str:
        return f"{name}:{tool}"
    return fm.streamable_http_app()


class Slow:
    """Wraps an upstream so that every request can be made to take a while (set .delay)."""
    def __init__(self, app):
        self.app, self.delay = app, 0.0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.delay:
            await asyncio.sleep(self.delay)
        await self.app(scope, receive, send)


class RoundRobin:
    """Plays a Kubernetes Service: sends each request to the next backend, and skips one that is down."""
    def __init__(self, backends):
        self.backends, self.i, self.served = backends, 0, []
        self.client = httpx.AsyncClient(timeout=30)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        order = [self.backends[(self.i + k) % len(self.backends)] for k in range(len(self.backends))]
        self.i += 1
        headers = {k.decode(): v.decode() for k, v in scope["headers"] if k.decode().lower() not in ("host", "content-length")}
        qs = scope["query_string"].decode()
        for base in order:
            try:
                r = await self.client.request(scope["method"], base + scope["path"] + (f"?{qs}" if qs else ""), headers=headers, content=body)
                break
            except httpx.TransportError:
                continue
        else:
            await send({"type": "http.response.start", "status": 502, "headers": []})
            await send({"type": "http.response.body", "body": b"no backend"})
            return
        self.served.append(r.headers.get("x-served-by"))
        hdrs = [(k.encode(), v.encode()) for k, v in r.headers.items() if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding")]
        hdrs.append((b"content-length", str(len(r.content)).encode()))
        await send({"type": "http.response.start", "status": r.status_code, "headers": hdrs})
        await send({"type": "http.response.body", "body": r.content})


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def metric_sum(text, prefix):
    return sum(float(l.rsplit(" ", 1)[1]) for l in text.splitlines() if l.startswith(prefix))


async def eventually(fn, seconds=4.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await fn():
            return True
        await asyncio.sleep(0.15)
    return False


async def main():
    ports = {n: free_port() for n in ("shop", "notes", "A", "B", "C", "D", "proxy")}
    shop_srv = uvicorn.Server(uvicorn.Config(make_upstream("shop", "list_orders", ports["shop"]), host="127.0.0.1", port=ports["shop"], log_level="warning"))
    slow = Slow(make_upstream("notes", "list_notes", ports["notes"]))
    notes_srv = uvicorn.Server(uvicorn.Config(slow, host="127.0.0.1", port=ports["notes"], log_level="warning"))
    ups = [asyncio.create_task(shop_srv.serve()), asyncio.create_task(notes_srv.serve())]
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(), "BOOTSTRAP_ADMIN_KEY": admin_key,
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "BOOTSTRAP_ADMIN_PASSWORD": "correct-horse-battery", "UPSTREAM_ALLOW_LOOPBACK": "true",
        "CALL_TIMEOUT_SECONDS": "3", "CONNECT_TIMEOUT_SECONDS": "2", "REFRESH_INTERVAL_SECONDS": "60", "REFRESH_TIMEOUT_SECONDS": "3", "FAIL_THRESHOLD": "2",
        "MCP_RATE_LIMIT_PER_MINUTE": str(LIMIT), "MCP_RATE_WINDOW_SECONDS": str(WINDOW), "CHAT_RATE_PER_MINUTE": "4",
        "LLM_API_KEY": "test-llm-key", "LLM_MODEL": "test-model",
        "SEED_SERVERS": json.dumps([{"slug": "shop", "name": "Shop", "url": f"http://127.0.0.1:{ports['shop']}/mcp"}]),
    })
    from app.config import Settings
    from app.main import create_asgi
    llm = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}))

    def replica(name, store, served_by):
        os.environ.update({"HOSTNAME": name, "RATE_LIMIT_STORE": store, "SERVED_BY_HEADER": str(served_by).lower()})
        root = create_asgi(Settings(), llm_transport=llm)
        srv = uvicorn.Server(uvicorn.Config(root, host="127.0.0.1", port=ports[name[-1].upper()], log_level="warning"))
        return root, srv

    # ---- two replicas start AT THE SAME TIME on an empty database ------------------------------------------------------
    rootA, srvA = replica("replica-a", "postgres", True)
    rootB, srvB = replica("replica-b", "postgres", True)
    taskA, taskB = asyncio.create_task(srvA.serve()), asyncio.create_task(srvB.serve())
    while not (srvA.started and srvB.started and shop_srv.started and notes_srv.started):
        await asyncio.sleep(0.05)
    baseA, baseB = f"http://127.0.0.1:{ports['A']}", f"http://127.0.0.1:{ports['B']}"
    hA, hB = httpx.AsyncClient(base_url=baseA, timeout=30), httpx.AsyncClient(base_url=baseB, timeout=30)
    A = {"Authorization": f"Bearer {admin_key}"}
    check("two replicas started together on an empty database, both ready", (await hA.get("/readyz")).text == "ok" and (await hB.get("/readyz")).text == "ok")
    conn = await asyncpg.connect(pg.get_uri())
    check("the shared-counter table was created once, safely", await conn.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_name = 'rate_hits'") == 1)

    # the control pair: the same code with the OLD in-memory counters
    rootC, srvC = replica("replica-c", "memory", False)
    rootD, srvD = replica("replica-d", "memory", False)
    taskC, taskD = asyncio.create_task(srvC.serve()), asyncio.create_task(srvD.serve())
    while not (srvC.started and srvD.started):
        await asyncio.sleep(0.05)
    hC, hD = httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports['C']}", timeout=30), httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports['D']}", timeout=30)
    await asyncio.sleep(1.5)

    # ---- which replica answered --------------------------------------------------------------------------------------
    r1, r2 = await hA.get("/api/me"), await hB.post("/mcp", json=LIST, headers={**A, **MCP_H})
    check("responses say which replica answered (API and MCP)", r1.headers.get("x-served-by") == "replica-a" and r2.headers.get("x-served-by") == "replica-b",
          f"{r1.headers.get('x-served-by')} / {r2.headers.get('x-served-by')}")
    check("the header is off unless asked for", "x-served-by" not in (await hC.get("/api/me")).headers)

    # ---- the shared counters themselves, under real concurrency ----------------------------------------------------------
    wA, wB = rootA.api.state.gw.windows, rootB.api.state.gw.windows
    results = await asyncio.gather(*[(wA if i % 2 else wB).check_and_add("unit:burst", 10, 5) for i in range(40)])
    check("40 simultaneous requests across two replicas: exactly 10 allowed", sum(1 for ok, _ in results if ok) == 10, str(sum(1 for ok, _ in results if ok)))
    check("the refused ones are told when to retry", all(1 <= retry <= 5 for ok, retry in results if not ok))
    once = await asyncio.gather(*[(wA if i % 2 else wB).once_per_window("unit:notice", 5) for i in range(30)])
    check("30 simultaneous 'first one only' asks: exactly one wins", sum(once) == 1)
    await wA.add("unit:count"); await wB.add("unit:count"); await wA.add("unit:count")
    check("counting sees what both replicas recorded", await wB.count("unit:count", 5) == 3)
    await asyncio.sleep(1.2)
    check("and old entries age out of the window", await wA.count("unit:count", 1) == 0)

    # ---- a real MCP session through a round-robin Service --------------------------------------------------------------
    rr = RoundRobin([baseA, baseB])
    proxy = uvicorn.Server(uvicorn.Config(rr, host="127.0.0.1", port=ports["proxy"], log_level="warning"))
    proxy_task = asyncio.create_task(proxy.serve())
    while not proxy.started:
        await asyncio.sleep(0.05)
    async with streamablehttp_client(f"http://127.0.0.1:{ports['proxy']}/mcp", headers=A) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            outs = [(await s.call_tool("shop__list_orders", {})).content[0].text for _ in range(3)]
    check("one MCP client session works through the load balancer", names == ["shop__list_orders"] and outs == ["shop:list_orders"] * 3, str(outs))
    check("and both replicas really served it", {"replica-a", "replica-b"} <= set(rr.served), str(sorted(set(rr.served))))
    rows = await conn.fetchval("SELECT count(*) FROM audit_log WHERE requested_name = 'shop__list_orders' AND outcome = 'success'")
    mA, mB = (await hA.get("/metrics")).text, (await hB.get("/metrics")).text
    pre = 'urumi_tool_calls_total{server="shop",tool="list_orders",outcome="success"'
    check("every call is in the one audit table, whichever replica ran it", rows == 3, str(rows))
    check("each replica counts its own calls; Prometheus adds them up", metric_sum(mA, pre) + metric_sum(mB, pre) == 3, f"A={metric_sum(mA, pre)} B={metric_sum(mB, pre)}")

    # ---- one dashboard session works on both replicas ------------------------------------------------------------------
    await hA.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    hB.cookies.set("urumi_session", hA.cookies.get("urumi_session"))
    check("a dashboard login on one replica is valid on the other", (await hB.get("/api/me")).json()["email"] == "admin@example.com")

    # ---- the request limit is ONE limit across replicas ------------------------------------------------------------------
    await asyncio.sleep(WINDOW + 0.5)
    codes = [(await (hA if i % 2 else hB).post("/mcp", json=LIST, headers={**A, **MCP_H})).status_code for i in range(16)]
    check(f"shared limit: of 16 requests split across both replicas, exactly {LIMIT} pass", codes.count(200) == LIMIT and codes.count(429) == 6, str(codes))
    check("a refused request says when to come back", int((await hA.post("/mcp", json=LIST, headers={**A, **MCP_H})).headers["retry-after"]) in range(1, WINDOW + 1))
    rows = await conn.fetchval("SELECT count(*) FROM audit_log WHERE requested_name = '(rate limit)'")
    check("a burst across two replicas wrote exactly ONE audit row", rows == 1, str(rows))
    await asyncio.sleep(WINDOW + 0.5)
    check("after the window the key works again, on either replica", (await hA.post("/mcp", json=LIST, headers={**A, **MCP_H})).status_code == 200 and (await hB.post("/mcp", json=LIST, headers={**A, **MCP_H})).status_code == 200)
    ccodes = [(await (hC if i % 2 else hD).post("/mcp", json=LIST, headers={**A, **MCP_H})).status_code for i in range(16)]
    check("CONTROL (in-memory counters): the same 16 requests all pass, so the limit has quietly doubled", ccodes.count(200) == 16, str(ccodes))

    # ---- the login lock-out is shared ------------------------------------------------------------------------------------
    bad = {"email": "carol@example.com", "password": "not-her-password"}
    for i in range(8):
        await (hA if i % 2 else hB).post("/api/auth/login", json=bad)
    check("shared lock-out: after 8 failures split across replicas, BOTH refuse the 9th", (await hA.post("/api/auth/login", json=bad)).status_code == 429 and (await hB.post("/api/auth/login", json=bad)).status_code == 429)
    bad2 = {"email": "dave@example.com", "password": "not-his-password"}
    for i in range(8):
        await (hC if i % 2 else hD).post("/api/auth/login", json=bad2)
    check("CONTROL: with in-memory counters the 9th attempt is not blocked, so a guesser gets twice the tries", (await hC.post("/api/auth/login", json=bad2)).status_code == 401)

    # ---- the chat limit is shared ---------------------------------------------------------------------------------------
    chat = lambda h: h.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=CSRF)
    cc = [(await chat(hA if i % 2 else hB)).status_code for i in range(6)]
    check("shared chat limit: 6 messages split across replicas, exactly 4 get through", cc.count(200) == 4 and cc.count(429) == 2, str(cc))
    await hC.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    hD.cookies.set("urumi_session", hC.cookies.get("urumi_session"))
    cc = [(await chat(hC if i % 2 else hD)).status_code for i in range(6)]
    check("CONTROL: with in-memory counters all 6 get through", cc.count(200) == 6, str(cc))

    # ---- an admin's change on one replica reaches the other at once --------------------------------------------------------
    sid = next(s["id"] for s in (await hA.get("/api/servers", headers=A)).json() if s["slug"] == "shop")
    tools_on = lambda h: h.get("/api/tools", headers=A)
    has = lambda h, name: (lambda r: name in [t["name"] for t in r.json()])
    async def listed(h, name):
        return name in [t["name"] for t in (await tools_on(h)).json()]
    started = time.monotonic()
    await hA.patch(f"/api/servers/{sid}", headers=A, json={"enabled": False})
    gone = await eventually(lambda: _not(listed(hB, "shop__list_orders")))
    check("disable on replica A: replica B drops the tool within seconds (refresh interval is 60 s)", gone, f"{time.monotonic() - started:.2f} s")
    await hB.patch(f"/api/servers/{sid}", headers=A, json={"enabled": True})
    check("enable on replica B: replica A gets it back", await eventually(lambda: listed(hA, "shop__list_orders")))
    r = await hA.post("/api/servers", headers=A, json={"slug": "notes", "name": "Notes", "url": f"http://127.0.0.1:{ports['notes']}/mcp"})
    nid = r.json()["id"]
    check("add on replica A: replica B lists the new server's tools", await eventually(lambda: listed(hB, "notes__list_notes")))
    # a slow re-check must not make the server's tools vanish while it is under way
    slow.delay = 0.6
    kick = asyncio.create_task(hA.post(f"/api/servers/{nid}/refresh", headers=A))
    samples = []
    for _ in range(8):
        samples += [await listed(hA, "notes__list_notes"), await listed(hB, "notes__list_notes")]
        await asyncio.sleep(0.12)
    await kick
    slow.delay = 0
    check("while a slow server is re-checked, its tools stay listed on BOTH replicas (no gap)", all(samples) and len(samples) == 16, f"{sum(samples)} of {len(samples)} looks")
    await hB.delete(f"/api/servers/{nid}", headers=A)
    check("remove on replica B: replica A drops them", await eventually(lambda: _not(listed(hA, "notes__list_notes"))))
    inv = (await hA.post("/api/users", headers=A, json={"email": "bob@example.com", "name": "Bob", "role": "member"})).json()
    B_ = {"Authorization": f"Bearer {inv['key']['api_key']}"}
    await hA.put("/api/permissions", headers=A, json={"changes": [{"role": "member", "server": "shop", "tool": "*", "allow": False}]})
    check("a permission rule set on A applies on B immediately", "shop__list_orders" not in [t["name"] for t in (await hB.get("/api/tools", headers=B_)).json()])
    await hA.put("/api/permissions", headers=A, json={"changes": [{"role": "member", "server": "shop", "tool": "*", "allow": None}]})

    # ---- an upstream outage is seen by both replicas --------------------------------------------------------------------------
    shop_srv.should_exit = True
    await ups[0]
    for _ in range(2):
        await hA.post(f"/api/servers/{sid}/refresh", headers=A)
    okA = await eventually(lambda: _not(listed(hA, "shop__list_orders")))
    okB = await eventually(lambda: _not(listed(hB, "shop__list_orders")))
    view = lambda root: (lambda u: (u.status, u.failures, (u.detail or "")[:40], len(u.tools)))(root.api.state.gw.registry.get("shop"))
    check("shop server stopped: BOTH replicas stop listing its tools", okA and okB, f"A={view(rootA)} B={view(rootB)}")
    await asyncio.sleep(WINDOW + 0.5)
    res = await hB.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "shop__list_orders", "arguments": {}}}, headers={**A, **MCP_H})
    check("a call to it fails clearly on either replica", "unreachable" in res.text and res.json()["result"]["isError"] is True, res.text[:100])
    shop_srv2 = uvicorn.Server(uvicorn.Config(make_upstream("shop", "list_orders", ports["shop"]), host="127.0.0.1", port=ports["shop"], log_level="warning"))
    ups[0] = asyncio.create_task(shop_srv2.serve())
    while not shop_srv2.started:
        await asyncio.sleep(0.05)
    await hB.post(f"/api/servers/{sid}/refresh", headers=A)
    check("shop server back: both replicas reconnect and list it again", await eventually(lambda: listed(hA, "shop__list_orders")) and await eventually(lambda: listed(hB, "shop__list_orders")))

    # ---- a replica dies; the service carries on ------------------------------------------------------------------------------
    rr.served.clear()
    srvB.should_exit = True
    await taskB
    await asyncio.sleep(WINDOW + 0.5)
    statuses = [(await rr_post(rr, ports, A)).status_code for _ in range(5)]
    check("replica B stopped: every request through the Service still succeeds", statuses == [200] * 5 or statuses.count(200) == 5, str(statuses))
    check("and all of them were answered by replica A", set(rr.served) == {"replica-a"}, str(set(rr.served)))
    check("the dashboard session still works on the surviving replica", (await hA.get("/api/me")).status_code == 200)

    print("ALL SCALING TESTS PASSED")
    await conn.close()
    proxy.should_exit = True
    for s in (srvA, srvC, srvD, shop_srv2, notes_srv):
        s.should_exit = True
    await asyncio.gather(taskA, taskC, taskD, proxy_task, ups[0], ups[1])


async def _not(coro):
    return not await coro


async def rr_post(rr, ports, key_headers):
    async with httpx.AsyncClient(timeout=30) as c:
        return await c.post(f"http://127.0.0.1:{ports['proxy']}/mcp", json=LIST, headers={**key_headers, **MCP_H})


if __name__ == "__main__":
    asyncio.run(main())
