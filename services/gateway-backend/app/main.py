"""Application entry point. Run with:  uvicorn app.main:create_asgi --factory"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .api import router
from .chat import router as chat_router
from .llm import LLMClient
from .config import Settings
from .db import Database
from .mcp_gateway import McpAuth, build_mcp
from .metrics import Metrics
from .permissions import Permissions
from .ratelimit import RateLimiter
from .registry import Registry
from .toolservice import ToolService
from .security import CredentialBox, hash_key, hash_password, parse_key_prefix

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gateway")


async def bootstrap(db: Database, settings: Settings) -> None:
    """First-run data: the admin user (key comes from a Secret) and the pre-registered servers."""
    key = settings.bootstrap_admin_key
    if key:
        prefix = parse_key_prefix(key)
        if prefix is None:
            raise RuntimeError("BOOTSTRAP_ADMIN_KEY does not have the form urm_<8 hex>_<secret>")
        user_id = await db.fetchval(
            """INSERT INTO users (email, name, role) VALUES ($1, 'Admin', 'admin')
               ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id""", settings.bootstrap_admin_email)
        await db.execute(
            "INSERT INTO api_keys (user_id, name, prefix, key_hash) VALUES ($1,'bootstrap',$2,$3) ON CONFLICT (prefix) DO NOTHING",
            user_id, prefix, hash_key(key))
    if settings.bootstrap_admin_password:
        # Set only while the admin has no password, so a later change in the dashboard is never overwritten.
        await db.execute("UPDATE users SET password_hash=$2 WHERE email=$1 AND password_hash IS NULL",
                         settings.bootstrap_admin_email,
                         await asyncio.to_thread(hash_password, settings.bootstrap_admin_password))
    for s in settings.seed_servers:   # operator-supplied config; admins can edit or remove these later
        await db.execute(
            "INSERT INTO mcp_servers (slug, name, url) VALUES ($1,$2,$3) ON CONFLICT (slug) DO NOTHING",
            s["slug"], s["name"], s["url"])


class Root:
    """Route /mcp to the MCP endpoint and everything else to FastAPI.

    (A Starlette Mount at /mcp would 307-redirect /mcp to /mcp/, which MCP clients do not follow.)
    """

    def __init__(self, api, mcp) -> None:
        self.api, self.mcp = api, mcp

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] in ("/mcp", "/mcp/"):
            return await self.mcp(scope, receive, send)
        return await self.api(scope, receive, send)


def create_asgi(settings: Settings | None = None, llm_transport=None) -> Root:
    settings = settings or Settings()
    db = Database(settings.database_url)
    box = CredentialBox(settings.encryption_key)
    registry = Registry(db, box, settings)
    permissions = Permissions(db)
    metrics = Metrics()
    limiter = RateLimiter(settings.mcp_rate_limit_per_minute, settings.mcp_rate_window_seconds)
    tools = ToolService(db, registry, permissions, metrics)
    llm = (LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout, transport=llm_transport)
           if settings.llm_api_key else None)
    _, manager = build_mcp(tools)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await db.connect()
        await bootstrap(db, settings)
        await registry.start()
        async with manager.run():
            log.info("gateway ready")
            yield
        await registry.stop()
        if llm:
            await llm.aclose()
        await db.close()

    api = FastAPI(title="Urumi MCP Gateway", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    api.state.gw = SimpleNamespace(db=db, registry=registry, box=box, settings=settings, tools=tools, permissions=permissions, llm=llm)
    api.include_router(router)
    api.include_router(chat_router)

    @api.get("/healthz")
    async def healthz() -> PlainTextResponse:       # liveness: the process is running
        return PlainTextResponse("ok")

    @api.get("/readyz")
    async def readyz(_: Request):                   # readiness: we can reach the database
        try:
            await db.fetchval("SELECT 1")
        except Exception:
            return JSONResponse({"ready": False}, status_code=503)
        return PlainTextResponse("ok")

    @api.get("/metrics")
    async def metrics_endpoint() -> PlainTextResponse:
        # Not routed by the Ingress (it sends only /api and /mcp here), so only in-cluster scrapers reach it.
        return PlainTextResponse(metrics.render(registry.all()), media_type="text/plain; version=0.0.4; charset=utf-8")

    return Root(api, McpAuth(manager, db, limiter, tools))
