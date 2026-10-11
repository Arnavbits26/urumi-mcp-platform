"""Run from services/mcp-notes:  python -m tests.test_server"""
import asyncio
import json
import os
import socket
import tempfile

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DB = os.path.join(tempfile.mkdtemp(), "data", "notes.db")        # the folder does not exist yet
os.environ.update({"AUTH_TOKEN": "notes-secret-token", "NOTES_DB": DB})
from app.server import build_app  # noqa: E402
from app.store import NoteStore  # noqa: E402


def check(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print("PASS", label, extra)


def texts(res):
    return " ".join(getattr(c, "text", "") for c in res.content)


async def serve(port):
    server = uvicorn.Server(uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server, task


async def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    os.environ.update({"MCP_HOST": "127.0.0.1", "MCP_PORT": str(port)})
    server, task = await serve(port)
    url, good = f"http://127.0.0.1:{port}", {"Authorization": "Bearer notes-secret-token"}

    async with httpx.AsyncClient() as http:
        h = {"Accept": "application/json, text/event-stream"}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        check("healthz is open", (await http.get(f"{url}/healthz")).text == "ok")
        check("no credential -> 401", (await http.post(f"{url}/mcp", json=body, headers=h)).status_code == 401)
        check("wrong token -> 401", (await http.post(f"{url}/mcp", json=body, headers={**h, "Authorization": "Bearer nope"})).status_code == 401)
        check("same token without 'Bearer' -> 401", (await http.post(f"{url}/mcp", json=body, headers={**h, "Authorization": "notes-secret-token"})).status_code == 401)
        check("an X-Api-Key header is not accepted here", (await http.post(f"{url}/mcp", json=body, headers={**h, "X-Api-Key": "notes-secret-token"})).status_code == 401)

    async with streamablehttp_client(f"{url}/mcp", headers=good) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            check("tools", names == ["add_note", "delete_note", "get_note", "list_notes"], str(names))
            a = json.loads(texts(await session.call_tool("add_note", {"title": "Weekly revenue", "body": "Total: 200"})))
            check("add_note returns the saved note", a["id"] == 1 and a["body"] == "Total: 200" and a["created_at"].endswith("+00:00"), str(a))
            await session.call_tool("add_note", {"title": "Ideas", "body": "x" * 300})
            res = await session.call_tool("list_notes", {})
            check("list_notes newest first, long text shortened", "Ideas" in texts(res).split("Weekly")[0] and "…" in texts(res))
            res = await session.call_tool("list_notes", {"search": "revenue"})
            check("search finds by title", "Weekly revenue" in texts(res) and "Ideas" not in texts(res))
            res = await session.call_tool("list_notes", {"search": "%"})
            check("a '%' in the search is plain text, not a wildcard", "Weekly revenue" not in texts(res))
            check("get_note returns the full text", "x" * 300 in texts(await session.call_tool("get_note", {"note_id": 2})))
            res = await session.call_tool("get_note", {"note_id": 99})
            check("missing note gives a clean error", res.isError and "no note with id 99" in texts(res))
            check("empty title rejected", (await session.call_tool("add_note", {"title": "", "body": "x"})).isError)
            check("delete_note", "deleted" in texts(await session.call_tool("delete_note", {"note_id": 2})))
            check("deleting twice is an error", (await session.call_tool("delete_note", {"note_id": 2})).isError)

    # ---- data survives a restart --------------------------------------------------------------
    server.should_exit = True
    await task
    check("note is on disk after the server stops", NoteStore(DB).get(1)["title"] == "Weekly revenue")
    server, task = await serve(port)
    async with streamablehttp_client(f"{url}/mcp", headers=good) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            check("note is still there after a restart", "Weekly revenue" in texts(await session.call_tool("list_notes", {})))
    server.should_exit = True
    await task

    del os.environ["AUTH_TOKEN"]
    try:
        build_app()
        check("refuses to start without AUTH_TOKEN", False)
    except RuntimeError:
        check("refuses to start without AUTH_TOKEN", True)
    print("NOTES TESTS PASSED")


asyncio.run(main())
