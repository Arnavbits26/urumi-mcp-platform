"""Notes MCP server: a small shared notebook for the team, saved on disk (SQLite).

The gateway signs in with a bearer token whose value comes from a Kubernetes Secret. Notes are
shared by the whole team: this server only sees the gateway's credential, not which person asked.
"""
from __future__ import annotations

import logging
import os
from typing import Annotated

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .auth import RequireCredential
from .store import NoteStore, NotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mcp-notes")


def build_server(store: NoteStore, host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    mcp = FastMCP(
        "notes", instructions="A shared notebook: save, find, read and delete short notes.",
        host=host, port=port, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @mcp.tool()
    def add_note(
        title: Annotated[str, Field(min_length=1, max_length=120, description="Short title")],
        body: Annotated[str, Field(min_length=1, max_length=5000, description="The note text")],
    ) -> dict:
        """Save a new note."""
        return store.add(title, body)

    @mcp.tool()
    def list_notes(
        search: Annotated[str | None, Field(description="Text to look for in titles and notes")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> list[dict]:
        """List notes, newest first, with a short preview of each."""
        return store.list(search, limit)

    @mcp.tool()
    def get_note(note_id: Annotated[int, Field(ge=1, description="The note's id")]) -> dict:
        """Read one note in full."""
        try:
            return store.get(note_id)
        except NotFound as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def delete_note(note_id: Annotated[int, Field(ge=1, description="The note's id")]) -> dict:
        """Delete a note permanently."""
        try:
            return store.delete(note_id)
        except NotFound as exc:
            raise ValueError(str(exc)) from exc

    return mcp


def build_app():
    token = os.environ.get("AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("AUTH_TOKEN is required: this server must never run without a credential.")
    store = NoteStore(os.environ.get("NOTES_DB", "/data/notes.db"))
    mcp = build_server(store, os.environ.get("MCP_HOST", "0.0.0.0"), int(os.environ.get("MCP_PORT", "8000")))
    return RequireCredential(mcp.streamable_http_app(), "authorization", token, prefix="Bearer ")


def main() -> None:
    uvicorn.run(build_app(), host=os.environ.get("MCP_HOST", "0.0.0.0"), port=int(os.environ.get("MCP_PORT", "8000")), log_level="info")


if __name__ == "__main__":
    main()
