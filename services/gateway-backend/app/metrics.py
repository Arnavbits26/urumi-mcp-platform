"""Prometheus metrics, written by hand in the text exposition format (no extra dependency).

Served at /metrics by the gateway. The Ingress routes only /api and /mcp to the gateway, so this is
reachable from inside the cluster (for a Prometheus scraper) and not from the internet.

Label values that a client could influence are controlled: a tool or server name is used as a label only
when it really exists on a registered server, otherwise it is reported as "unknown". Without that, a client
sending random names could create unlimited label values and exhaust memory.
"""
from __future__ import annotations

import time

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


def esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def labels(**kv) -> str:
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in kv.items()) + "}"


class Metrics:
    def __init__(self) -> None:
        self.calls: dict[tuple[str, str, str, str], int] = {}
        self.hist: dict[str, list[float]] = {}          # server -> [bucket counts..., sum, count]
        self.rate_limited = 0
        self.started = time.time()

    def observe_call(self, server: str, tool: str, outcome: str, source: str, seconds: float) -> None:
        key = (server, tool, outcome, source)
        self.calls[key] = self.calls.get(key, 0) + 1
        h = self.hist.setdefault(server, [0.0] * (len(BUCKETS) + 2))
        for i, bound in enumerate(BUCKETS):
            if seconds <= bound:
                h[i] += 1
        h[-2] += seconds
        h[-1] += 1

    def count_rate_limited(self) -> None:
        self.rate_limited += 1

    def render(self, upstreams) -> str:
        out: list[str] = []
        out += ["# HELP urumi_tool_calls_total Tool calls by server, tool, outcome and source.", "# TYPE urumi_tool_calls_total counter"]
        for (server, tool, outcome, source), n in sorted(self.calls.items()):
            out.append(f"urumi_tool_calls_total{labels(server=server, tool=tool, outcome=outcome, source=source)} {n}")
        out += ["# HELP urumi_tool_call_duration_seconds How long tool calls took, by server.", "# TYPE urumi_tool_call_duration_seconds histogram"]
        for server, h in sorted(self.hist.items()):
            for bound, count in zip(BUCKETS, h):
                out.append(f"urumi_tool_call_duration_seconds_bucket{labels(server=server, le=format(bound, 'g'))} {int(count)}")
            out.append(f"urumi_tool_call_duration_seconds_bucket{labels(server=server, le='+Inf')} {int(h[-1])}")
            out.append(f"urumi_tool_call_duration_seconds_sum{labels(server=server)} {h[-2]:.6f}")
            out.append(f"urumi_tool_call_duration_seconds_count{labels(server=server)} {int(h[-1])}")
        ups = sorted(upstreams, key=lambda u: u.slug)
        out += ["# HELP urumi_upstream_up 1 if the server is enabled and connected, otherwise 0.", "# TYPE urumi_upstream_up gauge"]
        out += [f"urumi_upstream_up{labels(server=u.slug)} {1 if (u.enabled and u.status == 'connected') else 0}" for u in ups]
        out += ["# HELP urumi_upstream_enabled 1 if an admin has the server switched on.", "# TYPE urumi_upstream_enabled gauge"]
        out += [f"urumi_upstream_enabled{labels(server=u.slug)} {1 if u.enabled else 0}" for u in ups]
        out += ["# HELP urumi_upstream_tools Number of tools the server offers.", "# TYPE urumi_upstream_tools gauge"]
        out += [f"urumi_upstream_tools{labels(server=u.slug)} {len(u.tools)}" for u in ups]
        out += ["# HELP urumi_rate_limited_total Requests refused because a key went over its limit.", "# TYPE urumi_rate_limited_total counter",
                f"urumi_rate_limited_total {self.rate_limited}",
                "# HELP urumi_process_start_time_seconds When this gateway process started (unix time).", "# TYPE urumi_process_start_time_seconds gauge",
                f"urumi_process_start_time_seconds {self.started:.0f}"]
        return "\n".join(out) + "\n"
