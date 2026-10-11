"""Runs scripts/mcp_client.py as a separate process against the real gateway and the real notes/utils servers,
with a scripted AI service. Run from services/gateway-backend:  python -m tests.test_external_client"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.security import generate_api_key

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = str(ROOT / "scripts" / "mcp_client.py")
NOTES_TOKEN, UTILS_KEY, LLM_KEY = "notes-token-SECRET-111", "utils-key-SECRET-222", "gsk_EXTERNAL_LLM_KEY_333"


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spawn(service, port, env):
    return subprocess.Popen([sys.executable, "-m", "app.server"], cwd=ROOT / "services" / service,
                            env={**os.environ, "MCP_HOST": "127.0.0.1", "MCP_PORT": str(port), **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def wait_healthy(port):
    async with httpx.AsyncClient() as http:
        for _ in range(100):
            try:
                if (await http.get(f"http://127.0.0.1:{port}/healthz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError(f"port {port} never became healthy")


def tc(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


SEEN = []   # what the AI service received (headers + body)


async def fake_ai(request: Request) -> JSONResponse:
    body = await request.json()
    SEEN.append({"auth": request.headers.get("authorization"), "body": json.dumps(body)})
    msgs = body["messages"]
    last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
    results = [m["content"] for m in msgs[last_user:] if m["role"] == "tool"]
    if not results:
        msg = {"role": "assistant", "content": None, "tool_calls": [tc("utils__calculate", {"expression": "80 + 120"})]}
    elif len(results) == 1:
        total = json.loads(results[0])["result"]
        msg = {"role": "assistant", "content": None, "tool_calls": [tc("notes__add_note", {"title": "Weekly revenue", "body": f"Total: {total}"})]}
    else:
        msg = {"role": "assistant", "content": "I added the orders up (200) and saved the total as a note."}
    return JSONResponse({"choices": [{"message": msg}]})


async def main():
    ports = {k: free_port() for k in ("notes", "utils", "gw", "ai", "dead")}
    notes_db = os.path.join(tempfile.mkdtemp(), "notes.db")
    procs = [spawn("mcp-notes", ports["notes"], {"AUTH_TOKEN": NOTES_TOKEN, "NOTES_DB": notes_db}),
             spawn("mcp-utils", ports["utils"], {"API_KEY": UTILS_KEY})]
    for k in ("notes", "utils"):
        await wait_healthy(ports[k])
    ai = uvicorn.Server(uvicorn.Config(Starlette(routes=[Route("/chat/completions", fake_ai, methods=["POST"])]),
                                       host="127.0.0.1", port=ports["ai"], log_level="warning"))
    ai_task = asyncio.create_task(ai.serve())

    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({"DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(), "BOOTSTRAP_ADMIN_KEY": admin_key,
                       "UPSTREAM_ALLOW_LOOPBACK": "true", "REFRESH_INTERVAL_SECONDS": "30"})
    os.environ.pop("LLM_API_KEY", None)
    from app.config import Settings
    from app.main import create_asgi
    gw = uvicorn.Server(uvicorn.Config(create_asgi(Settings()), host="127.0.0.1", port=ports["gw"], log_level="warning"))
    gw_task = asyncio.create_task(gw.serve())
    while not (gw.started and ai.started):
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{ports['gw']}"
    A = {"Authorization": f"Bearer {admin_key}"}
    http = httpx.AsyncClient(base_url=base, timeout=30)
    await http.post("/api/servers", headers=A, json={"slug": "notes", "name": "Notes", "url": f"http://127.0.0.1:{ports['notes']}/mcp", "auth_type": "bearer", "credential": NOTES_TOKEN})
    await http.post("/api/servers", headers=A, json={"slug": "utils", "name": "Utilities", "url": f"http://127.0.0.1:{ports['utils']}/mcp", "auth_type": "header", "auth_header_name": "X-Api-Key", "credential": UTILS_KEY})
    inv = (await http.post("/api/users", headers=A, json={"email": "userb@example.com", "name": "User B", "role": "member"})).json()
    b_key, b_key_id = inv["key"]["api_key"], inv["key"]["id"]

    def client(*args, key=b_key, llm=True, url=f"{base}/mcp", stdin=None):
        env = {k: v for k, v in os.environ.items() if k not in ("URUMI_API_KEY", "LLM_API_KEY", "URUMI_URL")}
        env.update({"URUMI_URL": url, "LLM_BASE_URL": f"http://127.0.0.1:{ports['ai']}", "LLM_MODEL": "test-model"})
        if key:
            env["URUMI_API_KEY"] = key
        if llm:
            env["LLM_API_KEY"] = LLM_KEY
        return asyncio.to_thread(subprocess.run, [sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True, timeout=90,
                                 input=stdin if stdin is not None else "", cwd=tempfile.gettempdir())

    r = await client("tools")
    check("tools: lists every server's tools", r.returncode == 0 and "8 tools" in r.stdout and "notes__add_note" in r.stdout and "utils__calculate" in r.stdout, r.stdout[:80].replace("\n", " ") + r.stderr[:120])
    r = await client("call", "utils__calculate", '{"expression": "6 * 7"}')
    check("call: runs one tool and prints the result", r.returncode == 0 and '"result": 42' in r.stdout, r.stdout.strip().replace("\n", " ")[:80])
    r = await client("call", "utils__calculate", "{bad json")
    check("call: bad JSON gives a friendly error, no traceback", r.returncode == 1 and "must be a JSON object" in r.stderr and "Traceback" not in r.stderr)
    r = await client("call", "utils__calculate", '{"expression": "__import__(1)"}')
    check("call: a failing tool exits 1 and shows why", r.returncode == 1 and "allowed" in r.stdout)
    r = await client("call", "nope__missing", "{}")
    check("call: unknown tool is reported, not crashed", r.returncode == 1 and "Unknown tool" in r.stdout)

    # ---- the AI-driven question: two different servers --------------------------------------------------
    SEEN.clear()
    r = await client("ask", "Add up my orders and save the total as a note.")
    out = r.stdout
    check("ask: the model used tools from TWO servers, in order", r.returncode == 0 and out.index("utils__calculate") < out.index("notes__add_note"), out.replace("\n", " | ")[:200] + r.stderr[:150])
    check("ask: prints the final answer", "saved the total as a note" in out.split("Answer:")[-1])
    check("the AI service got ITS key, never the gateway key", all(s["auth"] == f"Bearer {LLM_KEY}" and b_key not in s["body"] for s in SEEN) and len(SEEN) == 3)
    check("the gateway key and AI key are never printed", b_key not in out + r.stderr and LLM_KEY not in out + r.stderr and NOTES_TOKEN not in out and UTILS_KEY not in out)
    audit = (await http.get("/api/audit?limit=100", headers=A)).json()
    mine = [x for x in audit if x["user_email"] == "userb@example.com" and x["requested_name"] in ("utils__calculate", "notes__add_note") and x["outcome"] == "success"]
    check("audit: attributed to user B, recorded as an MCP client (not chat)", {x["requested_name"] for x in mine} >= {"utils__calculate", "notes__add_note"} and all(x["source"] == "mcp" for x in mine))
    r = await client("chat", stdin="Add up my orders and save the total as a note.\nexit\n")
    check("chat: works in a loop and exits cleanly", r.returncode == 0 and "Assistant: I added the orders up" in r.stdout, r.stdout[-120:].replace("\n", " "))

    # ---- failure handling -------------------------------------------------------------------------------------
    r = await client("tools", key="urm_deadbeef_notarealkey")
    check("wrong key: clear message, exit 2, no traceback", r.returncode == 2 and "rejected this API key" in r.stderr and "Traceback" not in r.stderr, r.stderr.strip()[:110])
    await http.delete(f"/api/keys/{b_key_id}", headers=A)
    r = await client("tools")
    check("revoked key: the very next call is rejected", r.returncode == 2 and "rejected this API key" in r.stderr, r.stderr.strip()[:110])
    r = await client("tools", url=f"http://127.0.0.1:{ports['dead']}/mcp")
    check("gateway down: says so, exit 2", r.returncode == 2 and "Cannot reach the gateway" in r.stderr, r.stderr.strip()[:110])
    r = await client("tools", key=None)
    check("missing key (no terminal): tells you what to set", r.returncode == 2 and "URUMI_API_KEY is not set" in r.stderr, r.stderr.strip()[:110])
    new = (await http.post(f"/api/users/{inv['user']['id']}/keys", headers=A, json={"name": "again"})).json()["api_key"]
    r = await client("ask", "hello", key=new, llm=False)
    check("ask without an AI key: tells you what to set", r.returncode == 2 and "LLM_API_KEY is not set" in r.stderr, r.stderr.strip()[:110])
    r = await client("tools", key=new)
    check("a newly issued key works at once", r.returncode == 0 and "8 tools" in r.stdout)

    for s in (gw, ai):
        s.should_exit = True
    await asyncio.gather(gw_task, ai_task)
    for p in procs:
        p.terminate()
        p.wait(timeout=10)
    pg.cleanup()
    print("ALL EXTERNAL CLIENT TESTS PASSED")


asyncio.run(main())
