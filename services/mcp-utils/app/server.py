"""Utilities MCP server: a safe calculator, simple statistics and time zones.

Stateless and dependency-free (no network calls), so it is a good example of a second upstream. The
gateway signs in with a custom header, X-Api-Key, whose value comes from a Kubernetes Secret.
"""
from __future__ import annotations

import logging
import os
import statistics as stats
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .auth import RequireCredential
from .calc import evaluate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mcp-utils")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"Unknown time zone '{name}'. Use a name like 'Asia/Kolkata' or 'UTC'.") from exc


def build_server(host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    mcp = FastMCP(
        "utils", instructions="Arithmetic, statistics and time-zone helpers.",
        host=host, port=port, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @mcp.tool()
    def calculate(expression: Annotated[str, Field(description="Arithmetic such as '80 + 120 * 2' or 'round(10 / 3, 2)'")]) -> dict:
        """Evaluate an arithmetic expression exactly. Use it for any sum, total or percentage instead of guessing."""
        return {"expression": expression, "result": evaluate(expression)}

    @mcp.tool()
    def statistics(numbers: Annotated[list[float], Field(min_length=1, max_length=1000, description="The numbers to summarise")]) -> dict:
        """Count, sum, mean, median, minimum and maximum of a list of numbers."""
        return {"count": len(numbers), "sum": sum(numbers), "mean": stats.fmean(numbers),
                "median": stats.median(numbers), "min": min(numbers), "max": max(numbers)}

    @mcp.tool()
    def current_time(timezone_name: Annotated[str, Field(description="IANA name, e.g. Asia/Kolkata. Default UTC")] = "UTC") -> dict:
        """The current date and time in a time zone."""
        now = datetime.now(timezone.utc).astimezone(_zone(timezone_name))
        return {"timezone": timezone_name, "iso": now.isoformat(timespec="seconds"),
                "weekday": now.strftime("%A"), "utc_offset": now.strftime("%z")}

    @mcp.tool()
    def convert_time(
        time: Annotated[str, Field(description="ISO date and time, e.g. 2026-10-12 09:30")],
        from_timezone: Annotated[str, Field(description="IANA name of the zone the time is in")],
        to_timezone: Annotated[str, Field(description="IANA name of the zone to convert to")],
    ) -> dict:
        """Convert a date and time from one time zone to another."""
        try:
            moment = datetime.fromisoformat(time)
        except ValueError as exc:
            raise ValueError("Give the time as ISO text, for example 2026-10-12 09:30.") from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_zone(from_timezone))
        out = moment.astimezone(_zone(to_timezone))
        return {"from": moment.isoformat(timespec="minutes"), "to": out.isoformat(timespec="minutes"), "timezone": to_timezone}

    return mcp


def build_app():
    api_key = os.environ.get("API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("API_KEY is required: this server must never run without a credential.")
    mcp = build_server(os.environ.get("MCP_HOST", "0.0.0.0"), int(os.environ.get("MCP_PORT", "8000")))
    return RequireCredential(mcp.streamable_http_app(), "x-api-key", api_key)


def main() -> None:
    uvicorn.run(build_app(), host=os.environ.get("MCP_HOST", "0.0.0.0"), port=int(os.environ.get("MCP_PORT", "8000")), log_level="info")


if __name__ == "__main__":
    main()
