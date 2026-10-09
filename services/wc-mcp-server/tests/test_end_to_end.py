"""End-to-end test: real MCP server over Streamable HTTP + real MCP client + fake WooCommerce.

Run:  python -m tests.test_end_to_end   (from services/wc-mcp-server)
"""
import asyncio
import json
import os
import socket

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

os.environ.setdefault("WC_BASE_URL", "http://fake-store")
os.environ.setdefault("WC_CONSUMER_KEY", "ck_test")
os.environ.setdefault("WC_CONSUMER_SECRET", "cs_test")

from app.config import Settings  # noqa: E402
from app.server import build_server  # noqa: E402
from app.wc_client import WooCommerceClient  # noqa: E402

PRODUCTS = [{"id": 1, "name": "Existing", "status": "publish", "price": "10", "regular_price": "10", "sku": "", "stock_status": "instock", "permalink": "http://x/p/1"}]
ORDERS = {15: {"id": 15, "status": "processing", "total": "80.00", "currency": "INR", "date_created": "2026-10-09T16:20:00",
               "billing": {"first_name": "zack", "last_name": "holmes", "email": "z@example.com", "phone": "1"},
               "payment_method_title": "Cash on delivery", "line_items": [{"product_id": 1, "name": "Test", "quantity": 1, "total": "80.00"}]}}
SEEN = {"params": None, "headers": None}


def fake_wc(request: httpx.Request) -> httpx.Response:
    SEEN["headers"] = dict(request.headers)
    if request.headers.get("authorization") is None:
        return httpx.Response(401, json={"message": "no auth"})
    if request.headers.get("x-forwarded-proto") != "https":
        return httpx.Response(401, json={"message": "needs https"})
    path = request.url.path.removeprefix("/wp-json/wc/v3")
    if request.method == "GET" and path == "/products":
        return httpx.Response(200, json=PRODUCTS)
    if request.method == "POST" and path == "/products":
        body = json.loads(request.content)
        product = {"id": 2, "permalink": "http://x/p/2", "sku": "", "stock_status": "instock", "price": body["regular_price"], **body}
        PRODUCTS.append(product)
        return httpx.Response(201, json=product)
    if request.method == "GET" and path == "/orders":
        SEEN["params"] = dict(request.url.params)
        return httpx.Response(200, json=list(ORDERS.values()))
    if path.startswith("/orders/"):
        oid = int(path.rsplit("/", 1)[1])
        if oid not in ORDERS:
            return httpx.Response(404, json={"message": "Invalid ID"})
        if request.method == "PUT":
            ORDERS[oid]["status"] = json.loads(request.content)["status"]
        return httpx.Response(200, json=ORDERS[oid])
    return httpx.Response(500, json={"message": "unexpected"})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def texts(res) -> str:
    """A list result arrives as one content block per item; join them."""
    return " ".join(c.text for c in res.content)


async def main() -> None:
    port = free_port()
    os.environ["MCP_PORT"], os.environ["MCP_HOST"] = str(port), "127.0.0.1"
    settings = Settings()
    wc = WooCommerceClient(settings, transport=httpx.MockTransport(fake_wc))
    app = build_server(settings, wc).streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    async with httpx.AsyncClient() as http:
        assert (await http.get(f"http://127.0.0.1:{port}/healthz")).text == "ok"
    print("PASS healthz")

    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            assert names == ["create_product", "get_order", "list_orders", "list_products", "update_order_status"], names
            print("PASS list tools:", names)

            res = await session.call_tool("create_product", {"name": "Mug", "regular_price": "499"})
            assert not res.isError, res
            assert json.loads(res.content[0].text)["name"] == "Mug"
            print("PASS create_product")

            res = await session.call_tool("create_product", {"name": "Bad", "regular_price": "abc"})
            assert res.isError
            print("PASS create_product rejects bad price")

            res = await session.call_tool("list_products", {})
            assert "Existing" in texts(res) and "Mug" in texts(res), texts(res)
            print("PASS list_products")

            res = await session.call_tool("list_orders", {"period": "today"})
            assert not res.isError and "zack holmes" in texts(res)
            assert "after" in SEEN["params"] and SEEN["params"]["after"].endswith("+00:00"), SEEN["params"]
            print("PASS list_orders period=today ->", SEEN["params"]["after"])

            res = await session.call_tool("get_order", {"order_id": 15})
            assert "z@example.com" in res.content[0].text
            print("PASS get_order")

            res = await session.call_tool("update_order_status", {"order_id": 15, "status": "completed"})
            assert not res.isError and ORDERS[15]["status"] == "completed"
            print("PASS update_order_status")

            res = await session.call_tool("update_order_status", {"order_id": 15, "status": "banana"})
            assert res.isError
            print("PASS update_order_status rejects bad status")

            res = await session.call_tool("get_order", {"order_id": 999})
            assert res.isError and "Not found" in res.content[0].text
            print("PASS missing order gives a clean error:", res.content[0].text)

    server.should_exit = True
    await task
    print("ALL TESTS PASSED")


asyncio.run(main())
