# 0002 - WooCommerce MCP server

**How it talks to the store: the WooCommerce REST API (v3).** Alternatives were WP-CLI (needs
exec access into the WordPress pod, which is a much bigger privilege and slow per call) and direct
SQL (bypasses WooCommerce's validation and hooks, so it can corrupt data). The REST API is
authenticated, validated by WooCommerce itself, versioned and documented, and needs only network
access to the WordPress Service.

**Credentials.** A read/write REST key is generated inside the cluster (`scripts/wc-api-keys.sh`),
written straight into the Secret `wc-api-credentials` and injected into the MCP server as env vars.
WooCommerce hashes the key at rest; the secret half is only in the Kubernetes Secret.

**Basic auth over HTTP.** WooCommerce accepts Basic auth only on HTTPS. Inside the cluster the MCP
server calls `http://wordpress` and sends `X-Forwarded-Proto: https`; the chart's `wp-config`
snippet turns that header into `$_SERVER['HTTPS']`. This is the same pattern used behind any
TLS-terminating ingress.

**Transport.** Streamable HTTP in stateless mode (`stateless_http=True`, JSON responses). Every
request stands alone, so any replica can serve it and no session state is lost on a restart.

**Not reachable from outside.** The Service is ClusterIP and there is no Ingress. A NetworkPolicy
template restricts callers to the gateway; it is switched on in v0.3.0 when the gateway exists.

**Tool design.** Five tools: `list_products`, `create_product`, `list_orders`, `get_order`,
`update_order_status`. Inputs are validated (price pattern, status whitelist, page limits). Outputs
are trimmed to useful fields. `list_orders` has a `period` shortcut (today / yesterday /
last_7_days, UTC) so a model need not know today's date. Store failures become short, safe tool
errors instead of stack traces.

**SDK pin.** `mcp>=1.30,<2`. Version 2 of the Python SDK renamed `FastMCP` and changed APIs; we
stay on 1.x until there is a reason to migrate.
