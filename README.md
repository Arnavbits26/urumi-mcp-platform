# urumi-mcp-platform

WooCommerce hosting with a team MCP gateway on Kubernetes (Urumi AI FDE internship, round 1).

An admin connects MCP servers once in a dashboard. Every team member then reaches all of them through one MCP
endpoint, from an AI client such as Claude Code, or through a chat page in the dashboard. Upstream credentials
never leave the gateway.

- Architecture, tradeoffs and failure handling: [`docs/system-design.md`](docs/system-design.md)
- Threat model: [`docs/threat-model.md`](docs/threat-model.md)
- Demo script for the definition of done: [`docs/demo-runbook.md`](docs/demo-runbook.md)
- Using the gateway from an external client: [`docs/external-client.md`](docs/external-client.md)
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

## Tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/gateway-backend/requirements.txt -r services/wc-mcp-server/requirements.txt \
            -r services/mcp-notes/requirements.txt -r services/mcp-utils/requirements.txt pgserver

(cd services/gateway-backend && for t in test_gateway_e2e test_dashboard_login test_ui_contract test_chat test_demo_upstreams test_external_client; do python -m tests.$t; done)
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
| 1 Production-like VPS | in progress, not yet shown | `deploy/`, a guide is being written |
| 6 Security hardening | partly | SSRF guard (`app/security.py`), encrypted credentials, non-root and read-only containers, NetworkPolicies on the three upstreams. Not done: default-deny everywhere, hardened database images |
| 7 Scaling | design only | the backend keeps no per-client state, but several replicas are not demonstrated; the chat limiter and login throttle are per replica |
| 8 Observability | audit log only | no metrics |
| 9 Upgrades and rollback | Helm-native, to be written up | |
| 2, 3, 4, 5, bonus multi-store | not attempted | |

## Honest limits

See the "Known gaps" list in the [threat model](docs/threat-model.md). The most important: there are no per-tool
permissions yet, so any member can call any tool the gateway offers.
