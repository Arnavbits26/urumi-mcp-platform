"""Thin async client for the WooCommerce REST API (v3)."""
from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class WooCommerceError(Exception):
    """A WooCommerce call failed. The message is safe to show to an MCP client."""


class WooCommerceClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=f"{settings.base_url}/wp-json/wc/v3",
            auth=(settings.consumer_key, settings.consumer_secret),
            # WooCommerce only accepts Basic auth on HTTPS. Inside the cluster we speak plain
            # HTTP to the WordPress Service, so we declare the original scheme as https. The
            # chart's wp-config snippet maps this header to $_SERVER['HTTPS'].
            headers={"X-Forwarded-Proto": "https", "Accept": "application/json"},
            timeout=settings.timeout,
            transport=transport,
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise WooCommerceError("The store did not respond in time (timeout).") from exc
        except httpx.HTTPError as exc:
            raise WooCommerceError(f"Could not reach the store: {type(exc).__name__}.") from exc

        if response.status_code in (401, 403):
            raise WooCommerceError("The store rejected the API credentials (HTTP %d)." % response.status_code)
        if response.status_code == 404:
            raise WooCommerceError("Not found in the store (HTTP 404).")
        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("message", "")
            except Exception:
                pass
            raise WooCommerceError(f"Store error (HTTP {response.status_code}). {detail}".strip())
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
