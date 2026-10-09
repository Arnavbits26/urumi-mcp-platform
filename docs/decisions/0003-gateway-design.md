# 0003 - Gateway design

**Shape.** One Python service (FastAPI + the MCP SDK) with PostgreSQL. It is an MCP *server* to
clients at `/mcp` and an MCP *client* to every registered upstream. The admin/self-service REST API
lives under `/api`.

**Tool names.** Every tool is exposed as `<server-slug>__<tool>`. Slugs are `[a-z][a-z0-9-]{1,23}`
(no underscore), so the first `__` always splits slug from tool name. We prefix *always*, not only on
collision, so a tool's name never changes when another server is added. Names over 64 characters (a
common client limit) are not listed.

**Aggregation and failure isolation.** `tools/list` is answered from an in-memory cache, never by
calling upstreams. A background loop checks each upstream independently with a hard timeout. After
`FAIL_THRESHOLD` consecutive failures an upstream becomes `unreachable` and its tools leave the list;
the others are untouched. Disabled servers are never contacted.

**Calls.** Each call opens a short-lived MCP session to the one upstream it needs, under a hard
timeout (`CALL_TIMEOUT_SECONDS`), and returns a clear tool error on timeout, refusal or failure.
Short-lived sessions keep the gateway stateless, so replicas need no shared session state.

**Credentials.**
- Client API keys: `urm_<8 hex>_<secret>`, 256 bits of randomness, shown once, only the SHA-256 is
  stored, looked up by the public 8-hex prefix, compared in constant time. Revocation takes effect on
  the next request because the database is checked every time.
- Upstream credentials: encrypted with Fernet (key from a Kubernetes Secret) before storage, never
  returned by the API, never logged, never sent to a client. The client's gateway key is never
  forwarded upstream; the gateway injects its own stored credential.

**SSRF guard.** URLs are validated when an admin adds or edits them and again before every connection
(this defeats DNS rebinding): http(s) only, no credentials in the URL, no link-local (cloud metadata),
multicast, reserved or unspecified addresses, no loopback outside local development. Private ranges
remain allowed because real upstreams are cluster-internal; a host allow-list is a planned step.

**Audit.** Every tool call writes one row (user, key prefix, server, tool, duration, outcome, short
detail), including rejected, timed-out and unreachable calls.

**Bootstrap.** The first admin's key comes from a Kubernetes Secret, and the WooCommerce server is
pre-registered from Helm values. Both are idempotent and never overwrite an admin's later edits.

**Schema management.** `CREATE TABLE IF NOT EXISTS` at start-up under an advisory lock. Simple and
safe for one release; a migration tool becomes worthwhile once the schema has to change in place.
