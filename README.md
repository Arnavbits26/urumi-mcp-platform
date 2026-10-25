# urumi-mcp-platform

WooCommerce hosting with a team MCP gateway on Kubernetes (Urumi AI FDE internship, round 1).

An admin connects MCP servers once in a dashboard. Every team member then reaches all of them through one MCP
endpoint, from an AI client such as Claude Code, or through a chat page in the dashboard. Upstream credentials
never leave the gateway.

- Architecture, tradeoffs and failure handling: [`docs/system-design.md`](docs/system-design.md)
- Threat model: [`docs/threat-model.md`](docs/threat-model.md)
- Demo script for the definition of done: [`docs/demo-runbook.md`](docs/demo-runbook.md)
- Using the gateway from an external client: [`docs/external-client.md`](docs/external-client.md)
- Production-like deployment on a k3s VM or VPS, with upgrade and rollback: [`docs/deploy-production.md`](docs/deploy-production.md)
- Design decisions, one page each: [`docs/decisions/`](docs/decisions/)

## What is in the repository

| Path | What |
|---|---|
| `charts/store` | WordPress + WooCommerce + MariaDB, volumes, ingress, automatic WooCommerce setup |
| `charts/wc-mcp` | MCP server for the store (my own code, Streamable HTTP, internal only) |
| `charts/gateway` | Gateway backend, dashboard, PostgreSQL, ingress |
| `charts/demo-mcp` | One chart used twice, for the notes and utils servers |
| `services/` | Source: `wc-mcp-server`, `gateway-backend`, `dashboard`, `mcp-notes`, `mcp-utils` |
| `deploy/` | Values files (`values-local.yaml` for the store, `values-gateway-local.yaml`, and so on) |
| `scripts/` | Cluster and secret helpers, image builds, the external client |

## Prerequisites (macOS or Linux)

Docker (4 CPUs and 6 GB or more for Docker Desktop), `k3d`, `kubectl`, `helm`, `openssl`, and Python 3.10+ for
the tests and the external client. A free AI key from Groq (console.groq.com) is needed only for the chat.

## Local setup

Run from the repository root, in this order. Each step is safe to repeat.

```bash
# 1. Cluster. Creates the k3d cluster "urumi"; the host's port 8080 goes to the ingress.
./scripts/k3d-up.sh

# 2. The store (takes a few minutes: it installs WordPress and WooCommerce)
./scripts/store-secrets.sh
helm upgrade --install store charts/store -n store -f deploy/values-local.yaml --wait --timeout 10m

# 3. The WooCommerce MCP server
./scripts/build-wc-mcp.sh
./scripts/wc-api-keys.sh
helm upgrade --install wc-mcp charts/wc-mcp -n store --wait --timeout 3m

# 4. Gateway and dashboard. llm-secret.sh asks for your AI key with a hidden prompt.
./scripts/gateway-secrets.sh
./scripts/gateway-admin-password.sh
./scripts/llm-secret.sh
./scripts/build-gateway.sh
./scripts/build-dashboard.sh
helm upgrade --install gateway charts/gateway -n gateway -f deploy/values-gateway-local.yaml --wait --timeout 5m

# 5. Lock the WooCommerce MCP server so only the gateway pods can reach it
helm upgrade wc-mcp charts/wc-mcp -n store -f deploy/values-wc-mcp-netpol.yaml --wait --timeout 3m

# 6. Two more MCP servers (notes, utils), then register them in the gateway
./scripts/build-demo-mcp.sh
./scripts/mcp-demo-secrets.sh
helm upgrade --install mcp-notes charts/demo-mcp -n mcp -f deploy/values-mcp-notes.yaml --wait --timeout 3m
helm upgrade --install mcp-utils charts/demo-mcp -n mcp -f deploy/values-mcp-utils.yaml --wait --timeout 3m
./scripts/register-demo-servers.sh
```

**Local domains.** `*.localtest.me` resolves to 127.0.0.1 through public DNS, and k3d forwards port 8080 to the
ingress, so no `/etc/hosts` edits are needed.

| What | Address |
|---|---|
| Storefront | http://store.localtest.me:8080 |
| WordPress admin | http://store.localtest.me:8080/wp-admin (user `admin`) |
| Dashboard | http://gateway.localtest.me:8080 (sign in as `admin@example.com`) |
| MCP endpoint | http://gateway.localtest.me:8080/mcp |

Passwords are random and live in Kubernetes Secrets. Print them when you need them:

```bash
kubectl -n store   get secret store-secrets   -o jsonpath='{.data.wp-admin-password}' | base64 -d; echo
kubectl -n gateway get secret gateway-secrets -o jsonpath='{.data.bootstrap-admin-password}' | base64 -d; echo
```

## Place an order

1. Open the storefront, add a product to the cart, check out with **Cash on delivery**.
2. In WordPress admin open WooCommerce, then Orders. The order is listed. (No products yet? Products, Add New, or ask the chat to create one.)

## Connect an external MCP client

Sign in to the dashboard, open **API keys** and create your own key. Then, for Claude Code:

```bash
claude mcp add --transport http urumi http://gateway.localtest.me:8080/mcp --header "Authorization: Bearer YOUR_KEY"
```

Other options (MCP Inspector, Cursor, and a free standalone script that needs no paid account) are in
[`docs/external-client.md`](docs/external-client.md). You see the tools of every enabled server, named
`woocommerce__...`, `notes__...`, `utils__...`.

## Use the chat agent

Open **Chat** in the dashboard and ask, for example, "What orders came in during the last 7 days?" or "Create a
product called Mug priced at 299". Under each answer it lists the tools it used, with arguments and results. Its
calls are audited under your name, tagged "via chat". Uses the AI service from step 4 (Groq's free tier by default;
the model is set in `deploy/values-gateway-local.yaml`).

## Production-like deployment

The same charts and scripts run on a fresh Ubuntu server with only values changed. On a Mac, a free Multipass VM
works as the server (about 4 GB of memory is enough):

```bash
./scripts/prod/export-images.sh        # build and save versioned images (Docker open)
./scripts/prod/vm-create.sh            # Ubuntu VM with k3s; prints two export lines to run
./scripts/prod/import-images.sh
./scripts/prod/deploy.sh               # all charts, secrets and registration
```

Full steps, the local-versus-production table, upgrade and rollback, and a real VPS variant:
[`docs/deploy-production.md`](docs/deploy-production.md).

## Tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/gateway-backend/requirements.txt -r services/wc-mcp-server/requirements.txt \
            -r services/mcp-notes/requirements.txt -r services/mcp-utils/requirements.txt pgserver

(cd services/gateway-backend && for t in test_gateway_e2e test_dashboard_login test_ui_contract test_chat test_demo_upstreams test_external_client test_permissions test_ratelimit_metrics; do python -m tests.$t; done)
(cd services/mcp-utils && python -m tests.test_server)
(cd services/mcp-notes && python -m tests.test_server)
(cd services/wc-mcp-server && python -m tests.test_end_to_end)
(cd services/dashboard && npm ci && npm test)
```

They use a real PostgreSQL (from the `pgserver` package), real MCP servers and scripted AI responses. No cluster.

## Requirements checklist

All mandatory items are implemented: Helm charts with local values, persistent storage for the store, ingress with
stable URLs, probes and resource limits on every workload, secrets only in Kubernetes Secrets, my own WooCommerce
MCP server (REST API, internal only), a gateway with name-collision handling and failure isolation, three
upstreams, roles, hashed API keys, an audit log, and the dashboard chat with a tool loop.

## Stand-out items

| Item | Status | Where |
|---|---|---|
| 2 Tool permissions | done (v0.9.0), per role, default allow | `services/gateway-backend/app/permissions.py`, `toolservice.py`, the dashboard's Permissions page, `tests/test_permissions.py`. See "Tool permissions" below |
| 1 Production-like VPS | done on a k3s VM, not public (a real VPS needs only DNS, an image import and TLS, all described) | `scripts/prod/`, `deploy/values-*-prod.yaml`, `docs/deploy-production.md`. Ran on a 4 GB Ubuntu VM at about 2.1 GB used. Public DNS and TLS were not exercised |
| 6 Security hardening | partly | SSRF guard (`app/security.py`), encrypted credentials, non-root and read-only containers, NetworkPolicies on the three upstreams. Not done: default-deny everywhere, hardened database images |
| 7 Scaling | partly: a per-key rate limit on `/mcp` (v0.9.1) | `app/ratelimit.py`. The backend keeps no per-client state, but several replicas are not demonstrated, and the counters are per replica |
| 8 Observability | partly: Prometheus `/metrics` (v0.9.1) and the audit log | `app/metrics.py`. No dashboards or alerts on top of it |
| 9 Upgrades and rollback | documented with commands, including a failed upgrade that Helm undoes by itself | `docs/deploy-production.md`, section 7 |
| 3, 4, 5, bonus multi-store | not attempted | |

## Tool permissions

An admin controls which tools each role may use. **Everything is allowed until you block it**, so a fresh install
behaves exactly as before. Rules are per role (`member` or `admin`). A rule names a server and a tool, or `*` for
every tool of that server, and the most specific rule wins (tool, then server, then the default).

A blocked tool is **hidden** from the tool list that external MCP clients and the dashboard chat see, and a direct
call to it is **rejected** (audit outcome `rejected`, reason "not permitted for the member role"). The upstream
server is never contacted. One check inside `ToolService` covers both the MCP endpoint and the chat.

In the dashboard, open **Permissions** (admins only). Each server has a Members column and an Admins column, and
**Make read-only for members** blocks that server's write-style tools (names starting with create, update, delete,
add, and similar) after showing you the list. By API, with an admin key:

```bash
curl -s -X PUT http://gateway.localtest.me:8080/api/permissions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"changes":[{"role":"member","server":"woocommerce","tool":"create_product","allow":false},
                  {"role":"member","server":"woocommerce","tool":"update_order_status","allow":false}]}'
```

`allow: true` or `false` sets a rule; `null` removes it. `GET /api/permissions` returns what each role may do and
which rule decided it. Removing a server removes its rules.

## Rate limits and metrics

**Rate limit.** Each API key may make 120 requests a minute to `/mcp` (Helm value `settings.mcpRateLimitPerMinute`;
`0` turns it off). Over the limit, the gateway answers **HTTP 429** with a `Retry-After` header and a JSON message,
and a client that backs off recovers as soon as its oldest request leaves the window. Each key has its own budget.
A burst of refused requests writes **one** audit row (outcome `rejected`, "rate limit exceeded"), not thousands. The
limit covers `/mcp` only, counters are kept per gateway replica, and requests with a wrong key get 401 and are not counted.

**Metrics.** `GET /metrics` on the gateway (port 8080) returns Prometheus text: `urumi_tool_calls_total` (by server,
tool, outcome and source), `urumi_tool_call_duration_seconds` (a histogram per server), `urumi_upstream_up`,
`urumi_upstream_enabled`, `urumi_upstream_tools` and `urumi_rate_limited_total`. The Ingress routes only `/api` and `/mcp`
to the gateway, so `/metrics` is reachable from inside the cluster and not from the internet. A tool or server name
becomes a label only if it really exists, so a client cannot create unlimited label values. To look at it:

```bash
kubectl -n gateway exec deploy/gateway-backend -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/metrics').read().decode())"
```

## Honest limits

See the "Known gaps" list in the [threat model](docs/threat-model.md). The most important: tool permissions are
per role and default-allow (nothing is blocked until an admin sets rules), and several gateway replicas are not demonstrated.
