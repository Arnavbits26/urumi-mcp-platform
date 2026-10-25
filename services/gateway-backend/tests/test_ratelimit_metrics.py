"""Per-key rate limit on /mcp, and the /metrics endpoint.

Real Postgres, a real MCP upstream and real HTTP. Two gateways run in one process: one with the limit off
(metrics checks) and one with a tiny limit and window (so the limit and recovery can be tested in seconds).
Run (from services/gateway-backend):  python -m tests.test_ratelimit_metrics
"""
import asyncio
import json
import os
import socket
import tempfile

import asyncpg
import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.metrics import esc
from app.ratelimit import RateLimiter
from app.security import generate_api_key

NOSEC = TransportSecuritySettings(enable_dns_rebinding_protection=False)
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_shop(port):
    fm = FastMCP("shop", host="127.0.0.1", port=port, stateless_http=True, json_response=True, transport_security=NOSEC)

    @fm.tool()
    def list_orders() -> str:
        """List orders."""
        return "orders:2"

    @fm.tool()
    def boom() -> str:
        """Always fails."""
        raise RuntimeError("nope")
    return fm.streamable_http_app()


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def metric(text: str, name: str):
    """The value of one exact metric line, or None."""
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.rsplit(" ", 1)[1])
    return None


async def main():
    # ---- unit: the limiter, with a fake clock ---------------------------------------------------------
    t = [0.0]
    rl = RateLimiter(3, 60, clock=lambda: t[0])
    check("unit: the first N requests pass", [rl.check("k")[0] for _ in range(3)] == [True] * 3)
    ok, retry = rl.check("k")
    check("unit: the next is refused, with a retry time", ok is False and retry == 60, str(retry))
    check("unit: another key has its own budget", rl.check("other")[0] is True)
    t[0] = 20
    check("unit: retry time shrinks as the window passes", rl.check("k") == (False, 40))
    t[0] = 61
    check("unit: allowed again once the window has passed", rl.check("k")[0] is True)
    t[0] = 0.0
    rl2 = RateLimiter(2, 60, clock=lambda: t[0])
    for _ in range(2):
        rl2.check("a")
    for _ in range(50):
        rl2.check("a")          # refused requests must not extend the window
    t[0] = 61
    check("unit: refused requests do not extend the window", rl2.check("a")[0] is True)
    check("unit: limit 0 means off", all(RateLimiter(0).check("x")[0] for _ in range(1000)) and RateLimiter(0).enabled is False)
    t[0] = 100
    rl3 = RateLimiter(5, 60, clock=lambda: t[0])
    check("unit: a burst is logged once per window", [rl3.first_rejection("k") for _ in range(4)] == [True, False, False, False])
    t[0] = 161
    check("unit: logged again in the next window", rl3.first_rejection("k") is True)
    rl3.check("idle")
    t[0] = 400
    rl3._sweep(t[0])
    check("unit: idle keys are forgotten (memory stays bounded)", "idle" not in rl3._hits and "k" not in rl3._logged)
    check("unit: label escaping", esc('a"b\\c\nd') == 'a\\"b\\\\c\\nd', esc('a"b\\c\nd'))

    # ---- two real gateways ------------------------------------------------------------------------------
    shop_port, gw1_port, gw2_port = free_port(), free_port(), free_port()
    shop = uvicorn.Server(uvicorn.Config(make_shop(shop_port), host="127.0.0.1", port=shop_port, log_level="warning"))
    shop_task = asyncio.create_task(shop.serve())
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(), "BOOTSTRAP_ADMIN_KEY": admin_key,
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "UPSTREAM_ALLOW_LOOPBACK": "true", "CALL_TIMEOUT_SECONDS": "3",
        "REFRESH_INTERVAL_SECONDS": "60", "REFRESH_TIMEOUT_SECONDS": "5",
        "SEED_SERVERS": json.dumps([{"slug": "shop", "name": "Shop", "url": f"http://127.0.0.1:{shop_port}/mcp"}]),
    })
    from app.config import Settings
    from app.main import create_asgi

    os.environ["MCP_RATE_LIMIT_PER_MINUTE"] = "0"
    gw1 = uvicorn.Server(uvicorn.Config(create_asgi(Settings()), host="127.0.0.1", port=gw1_port, log_level="warning"))
    gw1_task = asyncio.create_task(gw1.serve())
    os.environ["MCP_RATE_LIMIT_PER_MINUTE"], os.environ["MCP_RATE_WINDOW_SECONDS"] = "5", "3"
    gw2 = uvicorn.Server(uvicorn.Config(create_asgi(Settings()), host="127.0.0.1", port=gw2_port, log_level="warning"))
    gw2_task = asyncio.create_task(gw2.serve())
    while not (shop.started and gw1.started and gw2.started):
        await asyncio.sleep(0.05)
    b1, b2 = f"http://127.0.0.1:{gw1_port}", f"http://127.0.0.1:{gw2_port}"
    A = {"Authorization": f"Bearer {admin_key}"}
    h1, h2 = httpx.AsyncClient(base_url=b1, timeout=30), httpx.AsyncClient(base_url=b2, timeout=30)
    await asyncio.sleep(1.5)

    async def mcp(key, fn):
        async with streamablehttp_client(f"{b1}/mcp", headers={"Authorization": f"Bearer {key}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)

    # ---- metrics (gateway 1, limit off) -------------------------------------------------------------------
    r = await h1.get("/metrics")
    m = r.text
    check("/metrics answers with the Prometheus text type", r.status_code == 200 and r.headers["content-type"].startswith("text/plain"))
    check("before any call: the metric families are declared", "# TYPE urumi_tool_calls_total counter" in m and "# TYPE urumi_tool_call_duration_seconds histogram" in m)
    check("upstream gauges: the shop is up with 2 tools", metric(m, 'urumi_upstream_up{server="shop"}') == 1 and metric(m, 'urumi_upstream_tools{server="shop"}') == 2 and metric(m, "urumi_rate_limited_total") == 0)

    for _ in range(2):
        await mcp(admin_key, lambda s: s.call_tool("shop__list_orders", {}))
    await mcp(admin_key, lambda s: s.call_tool("shop__boom", {}))
    await mcp(admin_key, lambda s: s.call_tool("shop__nope_" + "x" * 30, {}))
    await mcp(admin_key, lambda s: s.call_tool("ghost__whatever", {}))
    m = (await h1.get("/metrics")).text
    check("success calls are counted by server, tool, outcome and source", metric(m, 'urumi_tool_calls_total{server="shop",tool="list_orders",outcome="success",source="mcp"}') == 2)
    check("a tool that failed is counted as an error", metric(m, 'urumi_tool_calls_total{server="shop",tool="boom",outcome="error",source="mcp"}') == 1)
    check("an unknown tool on a real server is rejected, with tool=unknown", metric(m, 'urumi_tool_calls_total{server="shop",tool="unknown",outcome="rejected",source="mcp"}') == 1)
    check("an unknown server is rejected, with server=unknown", metric(m, 'urumi_tool_calls_total{server="unknown",tool="unknown",outcome="rejected",source="mcp"}') == 1)
    check("client-chosen names never become labels (no cardinality blow-up)", "nope_" not in m and "ghost" not in m and "whatever" not in m)
    count = metric(m, 'urumi_tool_call_duration_seconds_count{server="shop"}')
    check("histogram: count, +Inf bucket and sum agree", count == 4 and metric(m, 'urumi_tool_call_duration_seconds_bucket{server="shop",le="+Inf"}') == count
          and metric(m, 'urumi_tool_call_duration_seconds_sum{server="shop"}') > 0, str(count))
    buckets = [metric(m, f'urumi_tool_call_duration_seconds_bucket{{server="shop",le="{b}"}}') for b in ("0.05", "0.1", "0.25", "0.5", "1", "2.5", "5", "10", "30")]
    check("histogram buckets only ever grow (cumulative)", all(a <= b for a, b in zip(buckets, buckets[1:])) and buckets[-1] <= count, str(buckets))
    check("metrics expose no secrets, keys or people", "urm_" not in m and "@" not in m and admin_key not in m)
    await h1.patch(f"/api/servers/{(await h1.get('/api/servers', headers=A)).json()[0]['id']}", headers=A, json={"enabled": False})
    m = (await h1.get("/metrics")).text
    check("disabling a server turns its gauges to 0 at once", metric(m, 'urumi_upstream_up{server="shop"}') == 0 and metric(m, 'urumi_upstream_enabled{server="shop"}') == 0)
    sid = (await h1.get("/api/servers", headers=A)).json()[0]["id"]
    await h1.patch(f"/api/servers/{sid}", headers=A, json={"enabled": True})
    check("limit 0: a flood of 40 requests is never refused", all([(await h1.post("/mcp", json=LIST, headers={**A, **MCP_HEADERS})).status_code == 200 for _ in range(40)]))

    # ---- the limit (gateway 2: 5 requests per 3 seconds) ------------------------------------------------------
    inv = (await h2.post("/api/users", headers=A, json={"email": "bob@example.com", "name": "Bob", "role": "member"})).json()
    B = {"Authorization": f"Bearer {inv['key']['api_key']}"}
    post = lambda hdr: h2.post("/mcp", json=LIST, headers={**hdr, **MCP_HEADERS})
    first = [(await post(A)).status_code for _ in range(5)]
    check("the first 5 requests pass", first == [200] * 5, str(first))
    r = await post(A)
    body = r.json()
    check("the 6th is refused with 429 and a JSON explanation", r.status_code == 429 and "rate limit exceeded" in body["error"] and 1 <= body["retry_after_seconds"] <= 3, str(body))
    check("a Retry-After header tells the client when to come back", 1 <= int(r.headers["retry-after"]) <= 3, r.headers.get("retry-after", ""))
    later = [(await post(A)).status_code for _ in range(15)]
    check("a flood keeps being refused", later == [429] * 15)
    check("another person's key has its own budget", (await post(B)).status_code == 200)
    check("the REST API is not limited (only /mcp)", all([(await h2.get("/api/servers", headers=A)).status_code == 200 for _ in range(10)]))
    check("bad keys get 401, not 429, and use up nothing", all([(await post({"Authorization": "Bearer urm_deadbeef_" + "x" * 40})).status_code == 401 for _ in range(10)]))
    conn = await asyncpg.connect(pg.get_uri())
    rows = await conn.fetch("SELECT user_email, outcome, detail, source FROM audit_log WHERE requested_name = '(rate limit)'")
    check("16 refused requests wrote exactly ONE audit row", len(rows) == 1 and rows[0]["user_email"] == "admin@example.com" and rows[0]["outcome"] == "rejected"
          and "rate limit exceeded" in rows[0]["detail"] and rows[0]["source"] == "mcp", str([dict(x) for x in rows]))
    m2 = (await h2.get("/metrics")).text
    check("every refused request is counted in the metrics", metric(m2, "urumi_rate_limited_total") == 16, str(metric(m2, "urumi_rate_limited_total")))
    await asyncio.sleep(3.3)
    check("after the window the key works again", (await post(A)).status_code == 200)
    for _ in range(4):
        await post(A)
    over = [(await post(A)).status_code for _ in range(3)]
    rows = await conn.fetch("SELECT 1 FROM audit_log WHERE requested_name = '(rate limit)'")
    await conn.close()
    check("a second burst is refused and logged once more", over == [429] * 3 and len(rows) == 2, f"{over} rows={len(rows)}")

    print("ALL RATE LIMIT AND METRICS TESTS PASSED")
    for s in (gw1, gw2, shop):
        s.should_exit = True
    await asyncio.gather(gw1_task, gw2_task, shop_task)


if __name__ == "__main__":
    asyncio.run(main())
