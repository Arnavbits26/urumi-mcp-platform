"""Handling many tools: 250 tools on 10 servers, reached through three small tools (search, schema, run).

Real Postgres, ten real MCP servers, a real MCP client and a scripted stand-in for the AI service.
Run (from services/gateway-backend):  python -m tests.test_many_tools
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

from app.compact import _norm
from app.security import generate_api_key

NOSEC = TransportSecuritySettings(enable_dns_rebinding_protection=False)
CSRF = {"X-Requested-With": "urumi-dashboard"}
MCP_H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

# ten servers, five kinds of thing each, five verbs per kind: 250 tools. Some words repeat across servers on purpose.
CATALOG = {
    "billing": ("Billing", ["invoice", "payment", "refund", "subscription", "customer"]),
    "crm": ("CRM", ["contact", "company", "deal", "lead", "note"]),
    "calendar": ("Calendar", ["event", "meeting", "room", "reminder", "slot"]),
    "email": ("Email", ["message", "draft", "thread", "label", "attachment"]),
    "tickets": ("Support Tickets", ["ticket", "comment", "assignee", "queue", "tag"]),
    "inventory": ("Inventory", ["item", "warehouse", "stock_level", "supplier", "purchase_order"]),
    "hr": ("HR", ["employee", "leave_request", "timesheet", "payslip", "department"]),
    "docs": ("Documents", ["document", "folder", "comment", "share_link", "template"]),
    "analytics": ("Analytics", ["report", "dashboard", "metric", "segment", "funnel"]),
    "shipping": ("Shipping", ["shipment", "label", "carrier", "rate_quote", "tracking_event"]),
}
plural = lambda w: w[:-1] + "ies" if w.endswith("y") else w + "s"
say = lambda w: w.replace("_", " ")

# what a person might really type, and the tool that should come back
QUERIES = [
    ("show me all invoices", "billing__list_invoices"),
    ("create a new calendar meeting", "calendar__create_meeting"),
    ("delete a support ticket", "tickets__delete_ticket"),
    ("check stock levels", "inventory__list_stock_levels"),
    ("update employee details", "hr__update_employee"),
    ("list tracking events", "shipping__list_tracking_events"),
    ("issue a refund", "billing__create_refund"),
    ("add a note", "crm__create_note"),
    ("create a share link", "docs__create_share_link"),
    ("show dashboards", "analytics__list_dashboards"),
    ("who are our suppliers", "inventory__list_suppliers"),
    ("get a purchase order", "inventory__get_purchase_order"),
    ("book a meeting room", "calendar__create_room"),
    ("remove a department", "hr__delete_department"),
    ("what carriers do we use", "shipping__list_carriers"),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_fn(verb, label):
    """One tool function per tool; it answers with its own label so the test can see where a call was routed."""
    if verb == "list":
        def fn(limit: int = 20, query: str = "") -> str:
            return label
    elif verb == "create":
        def fn(name: str, details: str = "") -> str:
            return label
    else:
        def fn(id: str) -> str:
            return label
    return fn


def make_server(slug, port):
    name, entities = CATALOG[slug]
    fm = FastMCP(slug, host="127.0.0.1", port=port, stateless_http=True, json_response=True, transport_security=NOSEC)
    for e in entities:
        for verb in ("list", "get", "create", "update", "delete"):
            tool = f"{verb}_{plural(e)}" if verb == "list" else f"{verb}_{e}"
            desc = {"list": f"List {plural(say(e))} in {name}. Supports filtering and paging.", "get": f"Get one {say(e)} from {name} by its id.",
                    "create": f"Create a new {say(e)} in {name}.", "update": f"Update an existing {say(e)} in {name}.",
                    "delete": f"Delete a {say(e)} from {name} permanently."}[verb]
            fm.add_tool(make_fn(verb, f"{slug}.{tool}"), name=tool, description=desc)
    return fm.streamable_http_app()


def tc(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeLLM:
    """Plays the AI service. 'findinvoices' makes it search, then run what it found; anything else it just answers."""
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.requests.append(payload)
        msgs = payload["messages"]
        last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        results = [m["content"] for m in msgs[last_user:] if m["role"] == "tool"]

        def reply(content=None, calls=None):
            msg = {"role": "assistant", "content": content}
            if calls:
                msg["tool_calls"] = calls
            return httpx.Response(200, json={"choices": [{"message": msg}]})
        if "findinvoices" in msgs[last_user]["content"]:
            if not results:
                return reply(calls=[tc("search_tools", {"query": "list invoices"})])
            if len(results) == 1:
                return reply(calls=[tc("run_tool", {"name": "billing__list_invoices", "arguments": {}})])
            return reply("RESULT: " + results[-1])
        return reply("ok")


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def texts(res) -> str:
    return " ".join(getattr(c, "text", "") for c in res.content)


async def main():
    check("unit: word normaliser", [_norm(w) for w in ("invoices", "companies", "status", "analysis", "address", "tags")] == ["invoice", "company", "status", "analysis", "address", "tag"])

    slugs = list(CATALOG)
    ports = {s: free_port() for s in slugs}
    ports["gw"] = free_port()
    ups = []
    for s in slugs:
        u = uvicorn.Server(uvicorn.Config(make_server(s, ports[s]), host="127.0.0.1", port=ports[s], log_level="warning"))
        ups.append((u, asyncio.create_task(u.serve())))
    pg = pgserver.get_server(tempfile.mkdtemp())
    admin_key, _, _ = generate_api_key()
    os.environ.update({
        "DATABASE_URL": pg.get_uri(), "ENCRYPTION_KEY": Fernet.generate_key().decode(), "BOOTSTRAP_ADMIN_KEY": admin_key,
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "BOOTSTRAP_ADMIN_PASSWORD": "correct-horse-battery", "UPSTREAM_ALLOW_LOOPBACK": "true",
        "CALL_TIMEOUT_SECONDS": "5", "REFRESH_INTERVAL_SECONDS": "60", "REFRESH_TIMEOUT_SECONDS": "10", "MCP_RATE_LIMIT_PER_MINUTE": "0",
        "LLM_API_KEY": "test-llm-key", "LLM_MODEL": "test-model", "CHAT_MAX_STEPS": "4", "CHAT_RATE_PER_MINUTE": "100",
        "SEED_SERVERS": json.dumps([{"slug": s, "name": CATALOG[s][0], "url": f"http://127.0.0.1:{ports[s]}/mcp"} for s in slugs]),
    })
    from app.config import Settings
    from app.main import create_asgi
    fake = FakeLLM()
    gw = uvicorn.Server(uvicorn.Config(create_asgi(Settings(), llm_transport=httpx.MockTransport(fake.handler)), host="127.0.0.1", port=ports["gw"], log_level="warning"))
    gw_task = asyncio.create_task(gw.serve())
    while not (gw.started and all(u.started for u, _ in ups)):
        await asyncio.sleep(0.05)
    base = f"http://127.0.0.1:{ports['gw']}"
    A = {"Authorization": f"Bearer {admin_key}"}
    http = httpx.AsyncClient(base_url=base, timeout=30)
    for _ in range(80):                                  # wait for the first check of all ten servers
        servers = (await http.get("/api/servers", headers=A)).json()
        if len(servers) == 10 and all(s["status"] == "connected" for s in servers):
            break
        await asyncio.sleep(0.25)
    check("ten servers connected, 25 tools each", [len(s["tools"]) for s in servers] == [25] * 10)

    async def mcp(path, key, fn):
        async with streamablehttp_client(f"{base}{path}", headers={"Authorization": f"Bearer {key}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)

    async def search(key=admin_key, **args):
        return json.loads(texts(await mcp("/mcp/compact", key, lambda s: s.call_tool("search_tools", args))))

    # ---- the two endpoints, and what each costs a model ---------------------------------------------------------
    full = await mcp("/mcp", admin_key, lambda s: s.list_tools())
    compact = await mcp("/mcp/compact", admin_key, lambda s: s.list_tools())
    full_size, compact_size = len(full.model_dump_json()), len(compact.model_dump_json())
    check("/mcp lists all 250 tools", len(full.tools) == 250)
    check("/mcp/compact lists exactly three", sorted(t.name for t in compact.tools) == ["get_tool_schema", "run_tool", "search_tools"])
    check("the three tools cost under 3% of the full list", compact_size < 0.03 * full_size, f"{compact_size} bytes vs {full_size} bytes ({100 * compact_size / full_size:.1f}%)")
    check("no key -> 401 on the new endpoint too", (await http.post("/mcp/compact", json={}, headers=MCP_H)).status_code == 401)

    # ---- how good is the search ---------------------------------------------------------------------------------
    ranks, started = [], time.perf_counter()
    for question, expected in QUERIES:
        names = [r["name"] for r in (await search(query=question, limit=20))["results"]]
        ranks.append(names.index(expected) + 1 if expected in names else None)
        print(f"     {question!r:42} -> {expected:34} rank {ranks[-1]}")
    ms = (time.perf_counter() - started) * 1000 / len(QUERIES)
    top1, top3 = sum(1 for r in ranks if r == 1), sum(1 for r in ranks if r and r <= 3)
    check("search: the right tool is in the top 3 for every question", top3 == len(QUERIES), f"top-1 {top1}/{len(QUERIES)}, top-3 {top3}/{len(QUERIES)}")
    check("search: the right tool is first for most questions", top1 >= 12, f"{top1}/{len(QUERIES)}")
    print(f"     (about {ms:.0f} ms per search including the round trip, over 250 tools)")
    r = await search(query="list", server="shipping", limit=20)
    check("search can be limited to one server", r["results"] and all(x["server"] == "shipping" for x in r["results"]))
    r = await search(query="list invoices", limit=2)
    check("results carry exact names, short descriptions and inputs", len(r["results"]) == 2 and r["results"][0]["name"] == "billing__list_invoices"
          and r["results"][0]["parameters"] == ["limit", "query"] and "List invoices" in r["results"][0]["description"] and r["total_matches"] > 2, str(r["results"][0]))
    check("a limit above 20 is clamped to 20", len((await search(query="list", limit=500))["results"]) == 20)
    check("a bad limit falls back to the default", len((await search(query="list", limit="lots"))["results"]) == 8)
    r = await search(query="quantum teleporter")
    check("no match: empty results and a hint", r["results"] == [] and "hint" in r)
    r = await search()
    check("no query: an overview of the servers", r["total_tools"] == 250 and len(r["servers"]) == 10 and r["servers"][0]["tools"] == 25 and len(r["servers"][0]["examples"]) == 3)
    check("a very long query is cut, not fatal", "results" in await search(query="invoice " * 500))

    # ---- schema and run -----------------------------------------------------------------------------------------
    sch = json.loads(texts(await mcp("/mcp/compact", admin_key, lambda s: s.call_tool("get_tool_schema", {"name": "crm__create_note"}))))
    check("get_tool_schema returns the real input schema", sch["name"] == "crm__create_note" and "name" in sch["inputSchema"]["properties"] and sch["inputSchema"]["required"] == ["name"])
    bad = await mcp("/mcp/compact", admin_key, lambda s: s.call_tool("get_tool_schema", {"name": "crm__nope"}))
    check("schema of an unknown tool is a clear error", bad.isError and "No such tool" in texts(bad))
    res = await mcp("/mcp/compact", admin_key, lambda s: s.call_tool("run_tool", {"name": "crm__create_note", "arguments": {"name": "hello"}}))
    check("run_tool runs the real tool and returns its result", not res.isError and texts(res) == "crm.create_note", texts(res))
    for label, args in (("no name", {}), ("a meta tool as the name", {"name": "search_tools"}), ("arguments that are not an object", {"name": "crm__create_note", "arguments": "x"}),
                        ("an unknown tool", {"name": "crm__nothing"}), ("a name with no server", {"name": "nothing"})):
        res = await mcp("/mcp/compact", admin_key, lambda s, a=args: s.call_tool("run_tool", a))
        check(f"run_tool refuses {label}", res.isError, texts(res)[:70])
    check("an unknown tool on the compact endpoint is a clear error", (await mcp("/mcp/compact", admin_key, lambda s: s.call_tool("crm__create_note", {}))).isError)

    # ---- the same audit, metrics and permissions as a direct call -------------------------------------------------
    conn = await asyncpg.connect(pg.get_uri())
    rows = await conn.fetch("SELECT user_email, source, outcome FROM audit_log WHERE requested_name = 'crm__create_note' AND outcome = 'success'")
    check("a run_tool call is audited under the person, as the real tool", len(rows) == 1 and rows[0]["user_email"] == "admin@example.com" and rows[0]["source"] == "mcp")
    check("searching is not a tool call and is not audited", await conn.fetchval("SELECT count(*) FROM audit_log WHERE requested_name IN ('search_tools','get_tool_schema')") == 0)
    m = (await http.get("/metrics")).text
    check("metrics count it as the real tool", 'urumi_tool_calls_total{server="crm",tool="create_note",outcome="success",source="mcp"} 1' in m)
    inv = (await http.post("/api/users", headers=A, json={"email": "bob@example.com", "name": "Bob", "role": "member"})).json()
    bob_key = inv["key"]["api_key"]
    await http.put("/api/permissions", headers=A, json={"changes": [{"role": "member", "server": "billing", "tool": "*", "allow": False}]})
    r = await search(bob_key, query="list invoices", limit=20)
    check("a member's search never shows tools their role may not use", all(x["server"] != "billing" for x in r["results"]) and len(r["results"]) > 0)
    check("the member's overview has 9 servers and 225 tools", (await search(bob_key))["total_tools"] == 225 and len((await search(bob_key))["servers"]) == 9)
    bad = await mcp("/mcp/compact", bob_key, lambda s: s.call_tool("get_tool_schema", {"name": "billing__list_invoices"}))
    check("a member cannot read the schema of a blocked tool", bad.isError)
    res = await mcp("/mcp/compact", bob_key, lambda s: s.call_tool("run_tool", {"name": "billing__list_invoices", "arguments": {}}))
    check("a member cannot run a blocked tool through run_tool", res.isError and "not permitted" in texts(res), texts(res)[:80])
    rows = await conn.fetch("SELECT outcome FROM audit_log WHERE requested_name = 'billing__list_invoices' AND user_email = 'bob@example.com'")
    check("and the refusal is audited", [r_["outcome"] for r_ in rows] == ["rejected"])
    await http.put("/api/permissions", headers=A, json={"changes": [{"role": "member", "server": "billing", "tool": "*", "allow": None}]})

    # ---- the chat, in each mode ---------------------------------------------------------------------------------------
    adm = httpx.AsyncClient(base_url=base, timeout=30)
    await adm.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    ask = lambda text, **kw: adm.post("/api/chat", json={"messages": [{"role": "user", "content": text}], **kw}, headers=CSRF)
    r = await ask("findinvoices please", tool_mode="compact")
    out = r.json()
    check("chat (search first): the model was offered 3 tools, not 250", r.status_code == 200 and out["tools_offered"] == 3 and out["tools_available"] == 250 and out["tool_mode"] == "compact",
          f"{out.get('tools_offered')} of {out.get('tools_available')}")
    first = fake.requests[-3]
    check("chat: the model-facing tools are the three, and the prompt explains them", sorted(t["function"]["name"] for t in first["tools"]) == ["get_tool_schema", "run_tool", "search_tools"]
          and "search_tools" in first["messages"][0]["content"])
    check("chat: the trace shows the search, then the tool that really ran", [(c["name"], c["outcome"]) for c in out["tool_calls"]] == [("search_tools", "success"), ("billing__list_invoices", "success")], str([c["name"] for c in out["tool_calls"]]))
    check("chat: the answer comes from the real tool's result", out["reply"] == "RESULT: billing.list_invoices")
    rows = await conn.fetch("SELECT user_email, source, outcome FROM audit_log WHERE requested_name = 'billing__list_invoices' AND source = 'chat'")
    check("chat: audited under the signed-in person as the real tool", len(rows) == 1 and rows[0]["user_email"] == "admin@example.com" and rows[0]["outcome"] == "success")
    out = (await ask("plain question", tool_mode="all")).json()
    check("chat (all tools): the model is offered all 250", out["tools_offered"] == 250 and out["tool_mode"] == "all" and len(fake.requests[-1]["tools"]) == 250)
    out = (await ask("plain question")).json()
    check("chat (automatic): above the threshold it switches to search first", out["tools_offered"] == 3 and out["tool_mode"] == "compact")
    check("chat: a bad mode is rejected", (await ask("x", tool_mode="everything")).status_code == 422)
    await conn.close()

    print("ALL MANY-TOOLS TESTS PASSED")
    gw.should_exit = True
    for u, _ in ups:
        u.should_exit = True
    await asyncio.gather(gw_task, *[t for _, t in ups])


if __name__ == "__main__":
    asyncio.run(main())
