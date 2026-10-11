"""Gateway + the REAL notes and utils servers (as separate processes) + a scripted AI service.

Proves: all three credential styles, credentials never leaving the gateway, one chat prompt that uses two
different servers, and a server being killed and brought back. Also runs scripts/register-demo-servers.sh
against a stand-in kubectl.   Run from services/gateway-backend:  python -m tests.test_demo_upstreams"""
import asyncio
import base64
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pgserver
import uvicorn
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.security import generate_api_key

ROOT = Path(__file__).resolve().parents[3]            # the repository root
SERVICES = ROOT / "services"
NOTES_TOKEN, UTILS_KEY = "notes-token-SECRET-111", "utils-key-SECRET-222"
CSRF = {"X-Requested-With": "urumi-dashboard"}


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def texts(res):
    return " ".join(getattr(c, "text", "") for c in res.content)


def spawn(service, port, extra_env):
    env = {**os.environ, "MCP_HOST": "127.0.0.1", "MCP_PORT": str(port), **extra_env}
    return subprocess.Popen([sys.executable, "-m", "app.server"], cwd=SERVICES / service, env=env,
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
    raise AssertionError(f"server on port {port} never became healthy")


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeLLM:
    """A model that solves 'add up my orders and save the total as a note' using two servers."""
    def __init__(self):
        self.requests = []

    def handler(self, request):
        payload = json.loads(request.content)
        self.requests.append(payload)
        msgs = payload["messages"]
        last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        results = [m["content"] for m in msgs[last_user:] if m["role"] == "tool"]
        if not results:
            return self._reply(tool_calls=[call("utils__calculate", {"expression": "80 + 120"})])
        if len(results) == 1:
            total = json.loads(results[0])["result"]
            return self._reply(tool_calls=[call("notes__add_note", {"title": "Weekly revenue", "body": f"Total: {total}"})])
        return self._reply(content="I added the orders up (200) and saved the total as a note.")

    @staticmethod
    def _reply(content=None, tool_calls=None):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return httpx.Response(200, json={"choices": [{"message": msg}]})


async def main():
    ports = {k: free_port() for k in ("notes", "utils", "gw")}
    notes_db = os.path.join(tempfile.mkdtemp(), "notes.db")
    procs = {
        "notes": spawn("mcp-notes", ports["notes"], {"AUTH_TOKEN": NOTES_TOKEN, "NOTES_DB": notes_db}),
        "utils": spawn("mcp-utils", ports["utils"], {"API_KEY": UTILS_KEY}),
    }
    for k in ("notes", "utils"):
        await wait_healthy(ports[k])

    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(), "BOOTSTRAP_ADMIN_KEY": admin_key,
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "BOOTSTRAP_ADMIN_PASSWORD": "correct-horse-battery",
        "UPSTREAM_ALLOW_LOOPBACK": "true", "REFRESH_INTERVAL_SECONDS": "1", "REFRESH_TIMEOUT_SECONDS": "3",
        "CONNECT_TIMEOUT_SECONDS": "2", "FAIL_THRESHOLD": "2", "LLM_API_KEY": "gsk_test", "LLM_MODEL": "test-model",
    })
    from app.config import Settings
    from app.main import create_asgi
    fake = FakeLLM()
    gw = uvicorn.Server(uvicorn.Config(create_asgi(Settings(), llm_transport=httpx.MockTransport(fake.handler)),
                                       host="127.0.0.1", port=ports["gw"], log_level="warning"))
    gw_task = asyncio.create_task(gw.serve())
    while not gw.started:
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{ports['gw']}"
    A = {"Authorization": f"Bearer {admin_key}"}
    http = httpx.AsyncClient(base_url=base, timeout=30)

    # ---- the registration script, run for real against a stand-in kubectl -------------------------------
    shim = Path(tempfile.mkdtemp()) / "kubectl"
    b64 = lambda s: base64.b64encode(s.encode()).decode()
    shim.write_text(f"""#!/usr/bin/env bash
case "$*" in
  *gateway-secrets*) printf %s {b64(admin_key)} ;;
  *notes-credentials*) printf %s {b64(NOTES_TOKEN)} ;;
  *utils-credentials*) printf %s {b64(UTILS_KEY)} ;;
  *) echo "unexpected kubectl call: $*" >&2; exit 1 ;;
esac
""")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    script_env = {**os.environ, "PATH": f"{shim.parent}:{os.environ['PATH']}",
                  "NOTES_URL": f"http://127.0.0.1:{ports['notes']}/mcp", "UTILS_URL": f"http://127.0.0.1:{ports['utils']}/mcp"}
    run = lambda: subprocess.run(["bash", str(ROOT / "scripts/register-demo-servers.sh"), base], env=script_env, capture_output=True, text=True, timeout=60)
    first = await asyncio.to_thread(run)
    check("register script: both servers registered and connected", first.returncode == 0 and "Registered notes (connected - 4 tools)" in first.stdout
          and "Registered utils (connected - 4 tools)" in first.stdout, first.stdout.strip().replace("\n", " | ") + first.stderr[:200])
    second = await asyncio.to_thread(run)
    check("register script is safe to run again", "already registered" in second.stdout and second.stdout.count("skipped") == 2, second.stdout.strip().replace("\n", " | "))
    check("register script never prints a credential", NOTES_TOKEN not in first.stdout + first.stderr and UTILS_KEY not in first.stdout + first.stderr)

    # ---- credentials: three styles, never exposed ---------------------------------------------------------
    servers = (await http.get("/api/servers", headers=A)).json()
    by = {s["slug"]: s for s in servers}
    check("registered with the right auth types", by["notes"]["auth_type"] == "bearer" and by["utils"]["auth_type"] == "header" and by["utils"]["auth_header_name"] == "X-Api-Key")
    listing = (await http.get("/api/servers", headers=A)).text
    check("API never returns a credential", NOTES_TOKEN not in listing and UTILS_KEY not in listing and by["notes"]["has_credential"] and by["utils"]["has_credential"])
    async with httpx.AsyncClient() as raw:
        h = {"Accept": "application/json, text/event-stream"}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        check("notes refuses a direct call with no token", (await raw.post(f"http://127.0.0.1:{ports['notes']}/mcp", json=body, headers=h)).status_code == 401)
        check("utils refuses a direct call with no key", (await raw.post(f"http://127.0.0.1:{ports['utils']}/mcp", json=body, headers=h)).status_code == 401)
        check("the gateway admin key does not work on an upstream", (await raw.post(f"http://127.0.0.1:{ports['notes']}/mcp", json=body, headers={**h, **A})).status_code == 401)

    async def mcp(key, fn):
        async with streamablehttp_client(f"{base}/mcp", headers={"Authorization": f"Bearer {key}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)

    tools = (await mcp(admin_key, lambda s: s.list_tools())).tools
    names = sorted(t.name for t in tools)
    check("one endpoint lists every server's tools, prefixed", names == sorted(
        [f"notes__{t}" for t in ("add_note", "delete_note", "get_note", "list_notes")] +
        [f"utils__{t}" for t in ("calculate", "convert_time", "current_time", "statistics")]), str(names))
    check("tools/list leaks no credential", NOTES_TOKEN not in json.dumps([t.model_dump() for t in tools]) and UTILS_KEY not in json.dumps([t.model_dump() for t in tools]))
    res = await mcp(admin_key, lambda s: s.call_tool("utils__calculate", {"expression": "6 * 7"}))
    check("external client -> gateway -> utils (header credential)", json.loads(texts(res))["result"] == 42)
    res = await mcp(admin_key, lambda s: s.call_tool("notes__add_note", {"title": "Direct", "body": "via MCP"}))
    check("external client -> gateway -> notes (bearer credential)", json.loads(texts(res))["id"] == 1)

    # ---- one prompt, two servers, as a signed-in member -------------------------------------------------
    inv = (await http.post("/api/users", headers=A, json={"email": "bob@example.com", "name": "Bob", "role": "member"})).json()
    bob = httpx.AsyncClient(base_url=base, timeout=60)
    await bob.post("/api/auth/login", json={"email": "bob@example.com", "password": inv["temporary_password"]})
    r = await bob.post("/api/chat", headers=CSRF, json={"messages": [{"role": "user", "content": "Add up my orders and save the total as a note."}]})
    out = r.json()
    used = [c["name"] for c in out["tool_calls"]]
    check("one prompt used tools from TWO different servers", r.status_code == 200 and used == ["utils__calculate", "notes__add_note"], str(used))
    check("the second tool used the first tool's answer", "Total: 200" in json.dumps(out["tool_calls"][1]["arguments"]))
    audit = (await http.get("/api/audit?limit=50", headers=A)).json()
    chat_rows = [x for x in audit if x["source"] == "chat"]
    check("audit: both chat calls attributed to the signed-in member", {(x["server"], x["user_email"]) for x in chat_rows} == {("utils", "bob@example.com"), ("notes", "bob@example.com")})
    check("no credential in the chat trace or the audit log", NOTES_TOKEN not in json.dumps(out) + json.dumps(audit) and UTILS_KEY not in json.dumps(out) + json.dumps(audit))
    saved = texts(await mcp(admin_key, lambda s: s.call_tool("notes__list_notes", {"search": "revenue"})))
    check("the note really was saved by the notes server", "Weekly revenue" in saved)

    schema_chars = len(json.dumps(fake.requests[0]["tools"]))
    check("8 tools cost about %d tokens per model request (WooCommerce adds ~5 more)" % (schema_chars // 4), schema_chars // 4 < 1800, f"{schema_chars} chars")

    # ---- kill one server: the others carry on; bring it back: it recovers -------------------------------------
    procs["notes"].terminate()
    procs["notes"].wait(timeout=10)
    await asyncio.sleep(4)
    st = {s["slug"]: s["status"] for s in (await http.get("/api/servers", headers=A)).json()}
    check("dead server shown as unreachable, the other still connected", st == {"notes": "unreachable", "utils": "connected"}, str(st))
    check("its tools disappear, the other server's remain", all(n.startswith("utils__") for n in (t.name for t in (await mcp(admin_key, lambda s: s.list_tools())).tools)))
    res = await mcp(admin_key, lambda s: s.call_tool("utils__calculate", {"expression": "1 + 1"}))
    check("calls to the healthy server still work", json.loads(texts(res))["result"] == 2)
    res = await mcp(admin_key, lambda s: s.call_tool("notes__list_notes", {}))
    check("calls to the dead server fail fast with a clear message", res.isError and "unreachable" in texts(res), texts(res)[:90])
    procs["notes"] = spawn("mcp-notes", ports["notes"], {"AUTH_TOKEN": NOTES_TOKEN, "NOTES_DB": notes_db})
    await wait_healthy(ports["notes"])
    await asyncio.sleep(3)
    st = {s["slug"]: s["status"] for s in (await http.get("/api/servers", headers=A)).json()}
    check("restarted server reconnects by itself", st["notes"] == "connected", str(st))
    check("and its notes survived the restart", "Weekly revenue" in texts(await mcp(admin_key, lambda s: s.call_tool("notes__list_notes", {}))))

    gw.should_exit = True
    await gw_task
    for p in procs.values():
        p.terminate()
        p.wait(timeout=10)
    pg.cleanup()
    print("ALL DEMO UPSTREAM TESTS PASSED")


asyncio.run(main())
