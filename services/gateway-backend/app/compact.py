"""Handling many tools: three small tools that find and run the real ones.

Ten servers can offer hundreds of tools, far too many to describe to a model on every request. Instead the
model is given three tools:

  search_tools     find tools by what they do (a few words), get exact names and inputs back
  get_tool_schema  the full description and input schema of ONE tool
  run_tool         run ONE tool by its exact name

Everything still goes through ToolService, so a person only ever finds and runs the tools their role may use,
and every run is audited, rate-limited and counted exactly like a direct call. Searching reads the cached tool
list and never contacts an upstream server.
"""
from __future__ import annotations

import re

from mcp import types

from .auth import AuthUser
from .registry import public_name
from .toolservice import ToolRun, ToolService

SEARCH, SCHEMA, RUN = "search_tools", "get_tool_schema", "run_tool"
META_NAMES = (SEARCH, SCHEMA, RUN)
DEFAULT_LIMIT, MAX_LIMIT, MAX_QUERY = 8, 20, 200

META_TOOLS = [
    types.Tool(
        name=SEARCH,
        description=("Find tools by what they do. Give a few words, for example 'list invoices' or 'create calendar event'. "
                     "Returns the best matches with their exact names and inputs. Call it with no query to see which servers exist."),
        inputSchema={"type": "object", "properties": {
            "query": {"type": "string", "description": "What you want to do, in a few words."},
            "server": {"type": "string", "description": "Only search this server (its short name)."},
            "limit": {"type": "integer", "description": "How many results, 1 to 20. Default 8."}}}),
    types.Tool(
        name=SCHEMA,
        description="Get the full description and input schema of ONE tool, by the exact name that search_tools returned.",
        inputSchema={"type": "object", "properties": {"name": {"type": "string", "description": "Exact tool name, like billing__list_invoices."}},
                     "required": ["name"]}),
    types.Tool(
        name=RUN,
        description=("Run ONE tool by its exact name, with its arguments. Find the name with search_tools first, and check the inputs "
                     "with get_tool_schema if you are unsure. Never guess a name."),
        inputSchema={"type": "object", "properties": {
            "name": {"type": "string", "description": "Exact tool name, like billing__list_invoices."},
            "arguments": {"type": "object", "description": "The tool's inputs."}}, "required": ["name"]}),
]

# ---- a small, explainable search: weighted word matches, no model and no index to maintain ---------------
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(("a an the to of for in on at by and or my me i we you it is are be this that with from please can could would should do does "
                   "who what which how where when our your their there all any some have has much many").split())
# Everyday verbs that mean the same as the verbs tools use. They add a half-weight match; they never replace the word.
_SYNONYMS = {"show": "list", "display": "list", "see": "list", "find": "search", "fetch": "get", "retrieve": "get",
             "make": "create", "add": "create", "new": "create", "issue": "create", "raise": "create", "book": "create", "schedule": "create",
             "submit": "create", "open": "create", "remove": "delete", "erase": "delete", "cancel": "delete",
             "change": "update", "modify": "update", "edit": "update"}


def _norm(word: str) -> str:
    """Crude singular form so 'orders' matches 'order' and 'invoices' matches 'invoice'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def _tokens(text: str) -> set[str]:
    return {_norm(w) for w in _words(text)}


def _query_weights(query: str) -> tuple[dict[str, float], int]:
    base = _tokens(query)
    weights: dict[str, float] = {w: 1.0 for w in base}
    for w in list(base):
        syn = _SYNONYMS.get(w)
        if syn and _norm(syn) not in weights:
            weights[_norm(syn)] = 0.5
    return weights, len(base)


def _score(weights: dict[str, float], base_count: int, raw_query: set[str], up, tool: types.Tool) -> float:
    name_t, server_t = _tokens(tool.name), _tokens(f"{up.slug} {up.name}")
    props = (tool.inputSchema or {}).get("properties", {}) if isinstance(tool.inputSchema, dict) else {}
    param_t, desc_t = _tokens(" ".join(props)), _tokens(tool.description or "")
    score, matched = 0.0, 0
    for word, w in weights.items():
        hit = False
        for tokens, points in ((name_t, 6), (server_t, 3), (param_t, 2), (desc_t, 1)):
            if word in tokens:
                score += points * w
                hit = True
        matched += 1 if hit and w == 1.0 else 0
    if base_count:
        score += 3 * matched / base_count          # reward tools that explain more of what was asked
    # The exact word as written beats a stem match: 'suppliers' prefers list_suppliers, 'supplier' prefers get_supplier.
    score += 1.5 * len(raw_query & set(_words(tool.name)))
    return score


def _params(tool: types.Tool) -> list[str]:
    schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
    required = set(schema.get("required") or [])
    return [f"{p}*" if p in required else p for p in list(schema.get("properties", {}))[:12]]


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


class Compact:
    def __init__(self, tools: ToolService) -> None:
        self.tools = tools

    async def search(self, user: AuthUser, query, server=None, limit=DEFAULT_LIMIT) -> dict:
        query = query.strip()[:MAX_QUERY] if isinstance(query, str) else ""
        try:
            limit = max(1, min(int(limit), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        pairs = await self.tools.visible(user)
        if isinstance(server, str) and server.strip():
            pairs = [(up, t) for up, t in pairs if up.slug == server.strip()]
        if not query:
            by_server: dict[str, dict] = {}
            for up, t in pairs:
                row = by_server.setdefault(up.slug, {"server": up.slug, "name": up.name, "tools": 0, "examples": []})
                row["tools"] += 1
                if len(row["examples"]) < 3:
                    row["examples"].append(t.name)
            return {"total_tools": len(pairs), "servers": sorted(by_server.values(), key=lambda r: r["server"]),
                    "hint": "Call search_tools again with a few words about what you want to do."}
        weights, base_count = _query_weights(query)
        raw_query = set(_words(query))
        scored = []
        for up, t in pairs:
            s = _score(weights, base_count, raw_query, up, t)
            if s > 0:
                scored.append((-s, len(t.name), public_name(up.slug, t.name), up, t))
        scored.sort(key=lambda x: x[:3])
        results = [{"name": name, "server": up.slug, "description": (t.description or "")[:160], "parameters": _params(t)}
                   for _, _, name, up, t in scored[:limit]]
        out = {"query": query, "total_matches": len(scored), "results": results}
        if not results:
            out["hint"] = "Nothing matched. Try different words, or call search_tools with no query to see the servers."
        return out

    async def schema(self, user: AuthUser, name) -> dict | None:
        if not isinstance(name, str):
            return None
        for up, t in await self.tools.visible(user):
            if public_name(up.slug, t.name) == name.strip():
                return {"name": name.strip(), "server": up.slug, "description": (t.description or "")[:2000], "inputSchema": t.inputSchema}
        return None

    async def run(self, user: AuthUser, name, arguments, source: str) -> ToolRun:
        if not isinstance(name, str) or not name.strip():
            return ToolRun(_error("run_tool needs 'name': the exact tool name from search_tools."), "rejected", "no tool name", 0, None, None)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return ToolRun(_error("'arguments' must be an object, for example {\"order_id\": 12}."), "rejected", "bad arguments", 0, None, None)
        if name.strip() in META_NAMES:
            return ToolRun(_error(f"'{name.strip()}' is not a catalogue tool. Use search_tools to find one, then run_tool with its exact name."),
                           "rejected", "meta tool", 0, None, None)
        return await self.tools.call(user, name.strip(), arguments, source=source)   # permissions, audit, metrics, timeouts: all here
