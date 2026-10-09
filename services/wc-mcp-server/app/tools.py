"""Pure functions that turn tool arguments into WooCommerce calls and trim the responses."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal

from .wc_client import WooCommerceClient, WooCommerceError

ORDER_STATUSES = {
    "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed",
}
Period = Literal["today", "yesterday", "last_7_days"]


def _product(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "status": p.get("status"),
        "price": p.get("price"),
        "regular_price": p.get("regular_price"),
        "sku": p.get("sku"),
        "stock_status": p.get("stock_status"),
        "permalink": p.get("permalink"),
    }


def _order(o: dict, detailed: bool = False) -> dict:
    billing = o.get("billing") or {}
    summary = {
        "id": o.get("id"),
        "status": o.get("status"),
        "total": o.get("total"),
        "currency": o.get("currency"),
        "date_created": o.get("date_created"),
        "customer": (f"{billing.get('first_name', '')} {billing.get('last_name', '')}").strip(),
        "payment_method": o.get("payment_method_title"),
        "items": [
            {"product_id": i.get("product_id"), "name": i.get("name"), "quantity": i.get("quantity"), "total": i.get("total")}
            for i in o.get("line_items", [])
        ],
    }
    if detailed:
        summary["billing_email"] = billing.get("email")
        summary["billing_phone"] = billing.get("phone")
        summary["shipping_total"] = o.get("shipping_total")
        summary["date_modified"] = o.get("date_modified")
        summary["customer_note"] = o.get("customer_note")
    return summary


def _period_bounds(period: Period, now: datetime | None = None) -> tuple[str, str | None]:
    """ISO-8601 bounds in UTC. The store timezone defaults to UTC in our setup."""
    now = now or datetime.now(timezone.utc)
    start_of_today = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    if period == "today":
        return start_of_today.isoformat(), None
    if period == "yesterday":
        return (start_of_today - timedelta(days=1)).isoformat(), start_of_today.isoformat()
    return (start_of_today - timedelta(days=7)).isoformat(), None


async def list_products(wc: WooCommerceClient, search: str | None, status: str, per_page: int, page: int) -> list[dict]:
    params: dict[str, Any] = {"per_page": per_page, "page": page, "status": status}
    if search:
        params["search"] = search
    return [_product(p) for p in await wc.request("GET", "/products", params=params)]


async def create_product(wc: WooCommerceClient, name: str, regular_price: str, description: str | None, status: str) -> dict:
    body: dict[str, Any] = {"name": name, "type": "simple", "regular_price": regular_price, "status": status}
    if description:
        body["description"] = description
    return _product(await wc.request("POST", "/products", json=body))


async def list_orders(
    wc: WooCommerceClient, status: str | None, period: Period | None,
    after: str | None, before: str | None, per_page: int, page: int,
) -> list[dict]:
    params: dict[str, Any] = {"per_page": per_page, "page": page, "orderby": "date", "order": "desc"}
    if status:
        if status not in ORDER_STATUSES:
            raise WooCommerceError(f"Invalid status '{status}'. Use one of: {sorted(ORDER_STATUSES)}.")
        params["status"] = status
    if period:
        params["after"], before_from_period = _period_bounds(period)
        if before_from_period:
            params["before"] = before_from_period
    if after:
        params["after"] = after
    if before:
        params["before"] = before
    return [_order(o) for o in await wc.request("GET", "/orders", params=params)]


async def get_order(wc: WooCommerceClient, order_id: int) -> dict:
    return _order(await wc.request("GET", f"/orders/{order_id}"), detailed=True)


async def update_order_status(wc: WooCommerceClient, order_id: int, status: str) -> dict:
    if status not in ORDER_STATUSES:
        raise WooCommerceError(f"Invalid status '{status}'. Use one of: {sorted(ORDER_STATUSES)}.")
    return _order(await wc.request("PUT", f"/orders/{order_id}", json={"status": status}))
