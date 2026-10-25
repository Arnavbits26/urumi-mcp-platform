# Urumi MCP Platform

**WooCommerce hosting with a team MCP gateway, on Kubernetes.** Built for the Urumi AI FDE internship, round 1.

An admin connects MCP servers once, in a dashboard. Every team member then uses all of them through **one MCP
endpoint**, from an AI client such as Claude Code, or through a **chat page** in the dashboard. Upstream credentials
never leave the gateway, and every tool call is written to an audit log under the person who made it.

```
  browser ------------+
                      |
  MCP client ---------+--> Ingress (Traefik) --+--> dashboard (React + nginx)
  /mcp + personal key                          |
                                               +--> gateway-backend ---------> PostgreSQL
                                               |    API, /mcp, chat agent,     users, hashed keys, sessions,
                                               |    permissions, audit         servers, audit log
                                               |        |
                                               |        | signs in to each server with its own credential
                                               |        |
                                               |    +---+----------------+------------------+
                                               |    v                    v                  v
                                               |  wc-mcp (my server)   mcp-notes          mcp-utils
                                               |    |                  (internal only: ClusterIP + NetworkPolicy)
                                               |    v
                                               +--> WordPress + WooCommerce ---> MariaDB   (the public storefront)
```

Everything runs in the cluster and installs with Helm. The **same charts** run on a laptop (k3d) and on a k3s
server; only the values files change.

## Contents

1. [Run it locally](#1-run-it-locally)
2. [Use it: place an order, chat, connect an MCP client](#2-use-it-place-an-order-chat-connect-an-mcp-client)
3. [Production-like deployment (k3s)](#3-production-like-deployment-k3s)
4. [System design and tradeoffs](#4-system-design-and-tradeoffs)
5. [Threat model](#5-threat-model)
6. [Bonus and stand-out items](#6-bonus-and-stand-out-items)
7. [Tests](#7-tests)
8. [Repository layout](#8-repository-layout)
9. [Known limits and troubleshooting](#9-known-limits-and-troubleshooting)

### Where to find each deliverable

| Deliverable | Where |
|---|---|
| README: local setup, production-like setup, placing an order, connecting a client, using the chat | this file, sections 1 to 3 |
| Source: dashboard | [`services/dashboard/`](services/dashboard/) |
| Source: backend and gateway | [`services/gateway-backend/app/`](services/gateway-backend/app/) (`api.py`, `mcp_gateway.py`, `toolservice.py`, `registry.py`, `security.py`) |
| Source: WooCommerce MCP server | [`services/wc-mcp-server/app/`](services/wc-mcp-server/app/) |
| Source: chat agent | [`services/gateway-backend/app/chat.py`](services/gateway-backend/app/chat.py) and [`llm.py`](services/gateway-backend/app/llm.py) |
| Helm charts and values (local and production) | [`charts/`](charts/) and [`deploy/`](deploy/) (`values-*-local.yaml`, `values-*-prod.yaml`) |
| System design and tradeoffs | [`docs/system-design.md`](docs/system-design.md), summarised in section 4 |
| Threat model | [`docs/threat-model.md`](docs/threat-model.md), summarised in section 5 |
| Bonus and stand-out items, and where to find them | section 6 |
| Demo script for the definition of done | [`docs/demo-runbook.md`](docs/demo-runbook.md) |

---

## 1. Run it locally

**Prerequisites** (macOS or Linux): Docker (Docker Desktop with 4 CPUs and 6 GB or more), `k3d`, `kubectl`, `helm`,
`openssl`, and Python 3.10+ for the tests and the standalone client. A free AI key from
[Groq](https://console.groq.com) is needed only for the chat; everything else works without it.

Run these from the repository root, in this order. Every step is safe to repeat. The first run is slow (image builds
and the WordPress install are the long parts).

```bash
# 1. Cluster. Creates the k3d cluster "urumi"; the host's port 8080 goes to the ingress.
./scripts/k3d-up.sh

# 2. The store (installs WordPress and WooCommerce, so it takes a few minutes)
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

# 6. Two more MCP servers (notes and utils), then register them in the gateway
./scripts/build-demo-mcp.sh
./scripts/mcp-demo-secrets.sh
helm upgrade --install mcp-notes charts/demo-mcp -n mcp -f deploy/values-mcp-notes.yaml --wait --timeout 3m
helm upgrade --install mcp-utils charts/demo-mcp -n mcp -f deploy/values-mcp-utils.yaml --wait --timeout 3m
./scripts/register-demo-servers.sh
```

**Check it worked:** `kubectl get pods -A` shows every pod `Running`, and the dashboard's **Servers** page shows
three servers (`woocommerce`, `notes`, `utils`) as **Connected**.

**Local domains.** `*.localtest.me` resolves to 127.0.0.1 through public DNS, and k3d forwards host port 8080 to the
cluster's ingress, so no `/etc/hosts` edits are needed.

| What | Address |
|---|---|
| Storefront | http://store.localtest.me:8080 |
| WordPress admin | http://store.localtest.me:8080/wp-admin (user `admin`) |
| Dashboard | http://gateway.localtest.me:8080 (sign in as `admin@example.com`) |
| MCP endpoint | http://gateway.localtest.me:8080/mcp |

**Passwords** are random and live only in Kubernetes Secrets. Print them when you need them:

```bash
kubectl -n store   get secret store-secrets   -o jsonpath='{.data.wp-admin-password}' | base64 -d; echo
kubectl -n gateway get secret gateway-secrets -o jsonpath='{.data.bootstrap-admin-password}' | base64 -d; echo
```

---

## 2. Use it: place an order, chat, connect an MCP client

### Place an order

1. Open the storefront at http://store.localtest.me:8080 and add a product to the cart. (No products yet? In the
   WordPress admin choose Products, Add New, or ask the chat to create one.)
2. Check out with **Cash on delivery**.
3. In the WordPress admin open **WooCommerce, then Orders**. The order is listed.

### Use the chat agent

Sign in to the dashboard and open **Chat**. Ask, for example, *"What orders came in during the last 7 days?"* or
*"Create a product called Mug priced at 299"*. Under each answer the chat lists the tools it used, with their
arguments and results. The agent acts **as you**: its calls go through the same gateway code as an external client's
and appear in the **Audit log** under your name, tagged "via chat". It uses any OpenAI-compatible AI service (Groq's
free tier by default; the model is set in `deploy/values-gateway-local.yaml`, the key in a Secret).

### Connect an external MCP client

1. In the dashboard open **API keys** and create your own key. It is shown once.
2. Add the gateway to your client. For Claude Code:

   ```bash
   claude mcp add --transport http urumi http://gateway.localtest.me:8080/mcp \
     --header "Authorization: Bearer YOUR_KEY"
   claude mcp list        # shows urumi as connected
   ```

3. Start the client. You see the tools of **every enabled server**, named `woocommerce__...`, `notes__...` and
   `utils__...`, without configuring any of the servers yourself. Disabling a server removes its tools.

Cursor, MCP Inspector and a free standalone script that needs no paid account are covered in
[`docs/external-client.md`](docs/external-client.md).

### Invite a teammate (admins)

On the **Team** page an admin invites a person, who then creates their own API key. Members use the tools but never
see any upstream credential. An admin can revoke a key at any time, and the next call with it is rejected.

### The whole demo flow

[`docs/demo-runbook.md`](docs/demo-runbook.md) walks through the assignment's definition of done with two users:
checkout, the chat creating a product and reporting an order, an external client as a second user, then revoking a
key, disabling a server and killing a pod.

---

## 3. Production-like deployment (k3s)

The same charts and scripts run on a fresh Ubuntu server with **only values changed**. On a Mac, a free Multipass VM
acts as the server (about 4 GB of memory is enough). On a real VPS the steps are the same; see the guide.

```bash
./scripts/prod/export-images.sh   # build and save versioned images (Docker open)
./scripts/prod/vm-create.sh       # Ubuntu VM with k3s; prints two export lines to run
./scripts/prod/import-images.sh
./scripts/prod/deploy.sh          # all charts, secrets and server registration
```

**What changes between local and production** (the charts do not):

| | Local (k3d) | Production-like (k3s on a VM or VPS) |
|---|---|---|
| Domain | `*.localtest.me:8080` | `store.<ip>.nip.io` and `gateway.<ip>.nip.io` on port 80, or a real domain |
| Ingress | k3d forwards 8080 to Traefik | Traefik on ports 80 and 443; TLS with cert-manager on a real domain (documented, not exercised) |
| Values files | `deploy/values-local.yaml`, `values-gateway-local.yaml` | `deploy/values-prod.yaml`, `values-gateway-prod.yaml`, `values-wc-mcp-prod.yaml`, `values-mcp-*-prod.yaml` |
| Host name set by | the values file | `scripts/prod/deploy.sh` (`--set host=...` from `BASE_DOMAIN`) |
| Images | `urumi/*:dev`, imported with `k3d image import` | `urumi/*:<VERSION>`, built once, saved to a file and imported (or pulled from a registry) |
| Storage | k3d's `local-path` | k3s's `local-path`; on a cloud, its block-storage class |
| Secrets | `scripts/*-secrets.sh` | the **same scripts**, run against the server, so secrets never travel through git |

**Upgrade and roll back with Helm.** Each component is its own release with numbered revisions:

```bash
helm history gateway -n gateway          # list revisions
helm rollback gateway 1 -n gateway       # return to a known-good revision (data volumes are kept)
```

`--atomic` makes Helm undo a bad upgrade by itself: upgrading to an image that does not exist leaves the old pod
serving, then Helm rolls back. I ran exactly that; the commands and output are in
[`docs/deploy-production.md`](docs/deploy-production.md) (section 7), which also covers the real-VPS variant and
the memory plan.

---

## 4. System design and tradeoffs

Full note: [`docs/system-design.md`](docs/system-design.md), with one-page decisions in
[`docs/decisions/`](docs/decisions/).

**Architecture choices.**

- The gateway is an MCP **server** to clients and an MCP **client** to every upstream, so a person configures one URL
  and one key, not one per server.
- The store is reached through **WooCommerce's REST API**: authenticated and validated by WooCommerce itself. WP-CLI
  would need exec access into the WordPress pod, and direct SQL would bypass WooCommerce's rules.
- **PostgreSQL holds all gateway state** (users, hashed keys, sessions, encrypted server credentials, permissions,
  audit log), so any gateway replica can serve any request.
- The dashboard chat and external clients share **one `ToolService`**, so permissions, timeouts and the audit log
  exist once.

**How tool aggregation, routing and failure handling work.**

- Every tool is exposed as `<server>__<tool>`. Server names cannot contain `__`, so the first `__` always splits
  correctly, and two servers can both have a tool called `search`. The prefix is always applied, so a tool's name
  never changes when another server is added.
- `tools/list` is answered from a **cache** that a background loop refreshes every 15 s, checking each upstream
  independently. A slow or dead upstream can never delay listing or another server's calls.
- Each call opens a short MCP session to the one upstream it needs, with the gateway's own stored credential.
  Timeouts: connect 5 s, call 30 s, refresh 10 s. A timeout is a clear tool error and an audit row.
- An upstream becomes `unreachable` after 2 failed checks in a row, its tools disappear, and it reconnects by itself
  when it returns. The dashboard shows the reason.

**What changes for production.** DNS names and ingress ports instead of `localtest.me:8080`; a block-storage class
and backups instead of `local-path`; secrets created on the server by the same scripts (sealed or external secrets
for a team); versioned images from a registry instead of `dev`; TLS and `cookieSecure: true` on a real domain.

---

## 5. Threat model

Full note: [`docs/threat-model.md`](docs/threat-model.md).

**What a member can do with a credential they never see.** They cannot read it: it is encrypted in the database,
never returned by any API, never logged, and sent only to the one server it belongs to. They can still *use* it
indirectly, because any tool they may call runs with the full power of that shared credential (the WooCommerce key
can create products and change order status). Limits: members cannot register or edit servers; every call is audited
under their name; a leaked key is revoked at once; and an admin can **block tools per role** (section 6) so that, for
example, members read orders but only admins change them. Nothing is blocked until an admin sets a rule.

**Why client tokens are not passed through to upstreams.** A member's key is meant for the gateway only. Forwarding
it would give a working key to every upstream, including a malicious one that could replay it against the gateway,
and would make upstreams accept tokens never issued for them (a confused-deputy problem). The gateway ends the
client's authentication itself and signs in to each upstream with a separate credential that only it holds, so an
upstream never learns who the member is.

**Untrusted upstreams: tool descriptions written to manipulate the model.** Only an admin can register a server.
Descriptions are length-capped and prefixed with the server's name; the chat gives tool output to the model only as
tool messages, never as user or system text, under a prompt that says results are data; tool calls, turns and output
size are capped; timeouts are strict; and URLs are checked against SSRF rules when registered and again on every
connection. **Residual risk:** a model can still be fooled into using a write tool the person is allowed to use, and
tool definitions are not pinned, so a server could change a description later.

**What one team member can do to another.** They cannot see each other's keys, audit rows, chat history or
sessions. They *can* change shared data through shared tools (products, order statuses, notes), because upstreams
cannot tell members apart; the audit log shows who did it. Admins can reset passwords and issue keys, so audit
attribution trusts admins.

---

## 6. Bonus and stand-out items

Details, commands and measurements for each item: [`docs/stand-out-features.md`](docs/stand-out-features.md).

| # | Item | Status | Where |
|---|---|---|---|
| 1 | Production-like VPS deployment | **Done** on a k3s VM (not public). Values-only changes, documented. Public DNS and TLS not exercised | `scripts/prod/`, `deploy/values-*-prod.yaml`, [`docs/deploy-production.md`](docs/deploy-production.md) |
| 2 | Tool permissions | **Done**, per role, default allow. Hidden from the tool list and rejected if called | [`docs/stand-out-features.md`](docs/stand-out-features.md#tool-permissions), `app/permissions.py`, the dashboard's Permissions page |
| 3 | OAuth on the gateway | Not attempted | |
| 4 | Handling many tools | **Done**: three tools (search, schema, run) at `/mcp/compact`, and a "Search first" mode in the chat | [`docs/stand-out-features.md`](docs/stand-out-features.md#handling-many-tools), `app/compact.py` |
| 5 | Hosted MCP servers | Not attempted | |
| 6 | Security hardening | **Partly**: SSRF guard, credentials encrypted at rest, non-root containers, NetworkPolicies on the three upstreams. Not done: default-deny everywhere, hardened database images | [`docs/stand-out-features.md`](docs/stand-out-features.md#security-hardening), `app/security.py` |
| 7 | Scaling | **Done**: per-key rate limit, and the gateway runs as several replicas that share their counters. One node; PostgreSQL is one pod | [`docs/stand-out-features.md`](docs/stand-out-features.md#scaling), `app/ratelimit.py`, `app/shared.py` |
| 8 | Observability | **Done, basic**: Prometheus `/metrics`, the audit log, and the reason an upstream is unreachable. No dashboards or alerts | [`docs/stand-out-features.md`](docs/stand-out-features.md#rate-limits-and-metrics), `app/metrics.py` |
| 9 | Upgrades and rollback | **Done**: documented, including a real failed upgrade that Helm rolled back | [`docs/deploy-production.md`](docs/deploy-production.md), section 7 |
| Bonus | Multi-store provisioning | **Not attempted.** How I would build it is sketched in [`docs/stand-out-features.md`](docs/stand-out-features.md#not-attempted-and-how-i-would-approach-them) | |

---

## 7. Tests

The tests use a real PostgreSQL (from the `pgserver` package), real MCP servers and scripted AI responses. No cluster
is needed.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/gateway-backend/requirements.txt -r services/wc-mcp-server/requirements.txt \
            -r services/mcp-notes/requirements.txt -r services/mcp-utils/requirements.txt pgserver

(cd services/gateway-backend && for t in test_gateway_e2e test_dashboard_login test_ui_contract test_chat test_demo_upstreams test_external_client test_permissions test_ratelimit_metrics test_many_tools test_scaling; do python -m tests.$t; done)
(cd services/mcp-utils && python -m tests.test_server)
(cd services/mcp-notes && python -m tests.test_server)
(cd services/wc-mcp-server && python -m tests.test_end_to_end)
(cd services/dashboard && npm ci && npm test)
bash scripts/prod/test-deploy-dry-run.sh
```

---

## 8. Repository layout

| Path | What |
|---|---|
| `charts/store` | WordPress, WooCommerce and MariaDB, volumes, ingress, and an idempotent WooCommerce setup job |
| `charts/wc-mcp` | The WooCommerce MCP server (my own code, Streamable HTTP, internal only) |
| `charts/gateway` | Gateway backend, dashboard, PostgreSQL, ingress |
| `charts/demo-mcp` | One chart used twice, for the notes and utils servers |
| `deploy/` | Values files for local and production |
| `services/` | Source: `wc-mcp-server`, `gateway-backend`, `dashboard`, `mcp-notes`, `mcp-utils` |
| `scripts/` | Cluster, secret and image helpers; `scripts/prod/` for the production-like deployment |
| `docs/` | System design, threat model, production guide, demo runbook, external clients, stand-out features, decisions |

---

## 9. Known limits and troubleshooting

**Limits, stated plainly** (the full prioritised list is at the end of the [threat model](docs/threat-model.md)):

- Tool permissions are per role and **default-allow**: until an admin sets rules, any member can call any tool.
- The production-like deployment ran on a local VM, not a public server, so public DNS and TLS were not exercised.
- PostgreSQL is a single pod, so the gateway scales but the database does not. There are no backups.
- NetworkPolicies protect the three upstreams but are not default-deny everywhere, and the databases and WordPress run
  the vendors' default images.
- Tool definitions are not pinned, so an upstream could change a description later.

| Problem | Fix |
|---|---|
| The chat says the AI key is missing | `./scripts/llm-secret.sh` (it restarts the gateway for you) |
| A pod is `Pending` or `ImagePullBackOff` | Run the matching `./scripts/build-*.sh`, then `kubectl get pods -A` |
| The dashboard shows an old page after an update | Hard-refresh, and `kubectl -n gateway rollout restart deployment/dashboard` |
| A server shows `unreachable` | Press **Check now** on the Servers page, and `kubectl -n mcp get pods` |
| Port 8080 is in use | Stop what uses it, or change the mapping in `scripts/k3d-up.sh` |
| The VM's pages do not open from the Mac | Allow your browser under macOS Local Network settings; see the production guide |
