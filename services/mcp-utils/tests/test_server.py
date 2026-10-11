"""Run from services/mcp-utils:  python -m tests.test_server"""
import asyncio
import json
import os
import socket

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

os.environ["API_KEY"] = "utils-secret-key"
from app.calc import evaluate  # noqa: E402
from app.server import build_app  # noqa: E402


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def texts(res):
    return " ".join(getattr(c, "text", "") for c in res.content)


def rejects(expr):
    try:
        evaluate(expr)
    except ValueError as exc:
        return str(exc)
    return None


async def main():
    # ---- calculator, directly --------------------------------------------------------------
    check("calc: precedence", evaluate("2 + 3 * 4") == 14)
    check("calc: brackets, division, round", evaluate("round((80 + 120) / 3, 2)") == 66.67)
    check("calc: functions", evaluate("max(3, 9, 4) - min(1, 2)") == 8 and evaluate("abs(-5)") == 5)
    for bad in ("__import__('os').system('id')", "open('/etc/passwd')", "().__class__", "[x for x in range(9)]", "lambda: 1",
                "a + 1", "'text' * 3", "True + 1", "9 ** 9 ** 9", "10 ** 101", "1 / 0", "(-8) ** 0.5", "round()", "1 +", "1" * 201,
                "-(" * 35 + "1" + ")" * 35):
        msg = rejects(bad)
        check(f"calc rejects {bad[:34]!r}", msg is not None, msg or "ACCEPTED!")
    check("calc: harmless extra brackets are fine", evaluate("((((((((((((((((((((((((((((((((1))))))))))))))))))))))))))))))))") == 1)
    check("calc: huge numbers are stopped", rejects("(10 ** 100) ** 100 * (10 ** 100) ** 100 * (10 ** 100) ** 100") is not None)

    # ---- server over HTTP ---------------------------------------------------------------
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    os.environ.update({"MCP_HOST": "127.0.0.1", "MCP_PORT": str(port)})
    server = uvicorn.Server(uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    url, good = f"http://127.0.0.1:{port}", {"X-Api-Key": "utils-secret-key"}

    async with httpx.AsyncClient() as http:
        check("healthz is open", (await http.get(f"{url}/healthz")).text == "ok")
        init = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        h = {"Accept": "application/json, text/event-stream"}
        check("no credential -> 401", (await http.post(f"{url}/mcp", json=init, headers=h)).status_code == 401)
        check("wrong credential -> 401", (await http.post(f"{url}/mcp", json=init, headers={**h, "X-Api-Key": "nope"})).status_code == 401)
        check("a bearer token is not accepted here", (await http.post(f"{url}/mcp", json=init, headers={**h, "Authorization": "Bearer utils-secret-key"})).status_code == 401)

    async with streamablehttp_client(f"{url}/mcp", headers=good) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            check("tools", names == ["calculate", "convert_time", "current_time", "statistics"], str(names))
            res = await session.call_tool("calculate", {"expression": "80 + 120"})
            check("calculate over MCP", json.loads(texts(res))["result"] == 200, texts(res))
            res = await session.call_tool("calculate", {"expression": "__import__('os')"})
            check("hostile expression gives a clean tool error", res.isError and "allowed" in texts(res), texts(res))
            res = await session.call_tool("statistics", {"numbers": [1, 2, 3, 10]})
            d = json.loads(texts(res))
            check("statistics", d == {"count": 4, "sum": 16, "mean": 4, "median": 2.5, "min": 1, "max": 10}, str(d))
            check("statistics rejects an empty list", (await session.call_tool("statistics", {"numbers": []})).isError)
            res = await session.call_tool("current_time", {"timezone_name": "Asia/Kolkata"})
            d = json.loads(texts(res))
            check("current_time in Kolkata has offset +0530", d["utc_offset"] == "+0530", str(d))
            res = await session.call_tool("convert_time", {"time": "2026-10-12 09:30", "from_timezone": "UTC", "to_timezone": "Asia/Kolkata"})
            check("convert_time UTC -> Kolkata", json.loads(texts(res))["to"].startswith("2026-10-12T15:00"), texts(res))
            res = await session.call_tool("current_time", {"timezone_name": "Mars/Base"})
            check("unknown zone gives a helpful error", res.isError and "Unknown time zone" in texts(res))

    server.should_exit = True
    await task
    # ---- must not start without a credential ---------------------------------------------------
    del os.environ["API_KEY"]
    try:
        build_app()
        check("refuses to start without API_KEY", False)
    except RuntimeError:
        check("refuses to start without API_KEY", True)
    print("UTILS TESTS PASSED")


asyncio.run(main())
