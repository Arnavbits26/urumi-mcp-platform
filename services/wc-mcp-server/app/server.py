"""WooCommerce MCP server (Streamable HTTP, stateless).

Stateless mode means any replica can answer any request, which keeps this service easy to scale.
The server holds the WooCommerce API credentials; clients never see them.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import tools
from .config import Settings
from .wc_client import WooCommerceClient, WooCommerceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("wc-mcp")


def build_server(settings: Settings, wc: WooCommerceClient | None = None) -> FastMCP:
    wc = wc or WooCommerceClient(settings)

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(settings.allowed_hosts),
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=[],
    )
    mcp = FastMCP(
        "woocommerce",
        instructions="Tools to read and manage products and orders of one WooCommerce store.",
        host=settings.host,
        port=settings.port,
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> PlainTextResponse:  # liveness + readiness: the process is up
        return PlainTextResponse("ok")

    @mcp.tool()
    async def list_products(
        search: Annotated[str | None, Field(description="Text to search in product names")] = None,
        status: Annotated[Literal["publish", "draft", "private", "any"], Field(description="Product status filter")] = "any",
        per_page: Annotated[int, Field(ge=1, le=50, description="Results per page")] = 20,
        page: Annotated[int, Field(ge=1, description="Page number")] = 1,
    ) -> list[dict]:
        """List products in the store."""
        return await _run(tools.list_products(wc, search, status, per_page, page))

    @mcp.tool()
    async def create_product(
        name: Annotated[str, Field(min_length=1, max_length=200, description="Product name")],
        regular_price: Annotated[str, Field(pattern=r"^\d+(\.\d{1,2})?$", description="Price as a plain number, e.g. '499' or '19.99'")],
        description: Annotated[str | None, Field(max_length=5000, description="Product description")] = None,
        status: Annotated[Literal["publish", "draft"], Field(description="'publish' shows it on the storefront")] = "publish",
    ) -> dict:
        """Create a simple product. Published products appear on the storefront."""
        return await _run(tools.create_product(wc, name, regular_price, description, status))

    @mcp.tool()
    async def list_orders(
        status: Annotated[str | None, Field(description="processing, completed, pending, on-hold, cancelled, refunded or failed")] = None,
        period: Annotated[tools.Period | None, Field(description="Shortcut date range: today, yesterday or last_7_days (UTC)")] = None,
        after: Annotated[str | None, Field(description="Only orders created after this ISO-8601 datetime")] = None,
        before: Annotated[str | None, Field(description="Only orders created before this ISO-8601 datetime")] = None,
        per_page: Annotated[int, Field(ge=1, le=50)] = 20,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> list[dict]:
        """List orders, newest first. Use period='today' for 'what orders came in today'."""
        return await _run(tools.list_orders(wc, status, period, after, before, per_page, page))

    @mcp.tool()
    async def get_order(order_id: Annotated[int, Field(ge=1, description="Order ID")]) -> dict:
        """Get full details of one order."""
        return await _run(tools.get_order(wc, order_id))

    @mcp.tool()
    async def update_order_status(
        order_id: Annotated[int, Field(ge=1, description="Order ID")],
        status: Annotated[Literal["pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"], Field(description="New status")],
    ) -> dict:
        """Change the status of an order."""
        return await _run(tools.update_order_status(wc, order_id, status))

    return mcp


async def _run(coro):
    """Turn store failures into clean tool errors instead of stack traces."""
    try:
        return await coro
    except WooCommerceError as exc:
        log.warning("tool failed: %s", exc)
        raise ValueError(str(exc)) from exc


def main() -> None:
    settings = Settings()
    log.info("Starting WooCommerce MCP server on %s:%s (store: %s)", settings.host, settings.port, settings.base_url)
    build_server(settings).run(transport="streamable-http")


if __name__ == "__main__":
    main()
