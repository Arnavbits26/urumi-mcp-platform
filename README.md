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

Everything runs in the cluster and installs with Helm. The **same charts** run on a laptop and on a k3s server; only
the values files change.

**On this page:** [Quick start](#quick-start) · [Try it](#try-it) · [Set it up your way](#set-it-up-your-way) ·
[Run it on a server](#run-it-on-a-server-k3s) · [How it works](#how-it-works) ·
[Bonus and stand-out items](#bonus-and-stand-out-items) · [Reference](#reference)

---

## Quick start

### 1. Install what it needs

Works on macOS and Linux (on Windows, use WSL2; not tested). Docker needs about 4 CPUs and 6 GB of memory.

| Tool | Why | Install |
|---|---|---|
| Docker | builds images and runs the local cluster | [docker.com](https://docs.docker.com/get-docker/) (macOS: Docker Desktop) |
| `k3d` | a small Kubernetes inside Docker | [k3d.io](https://k3d.io) |
| `kubectl` | talks to the cluster | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |
| `helm` | installs the charts | [helm.sh](https://helm.sh/docs/intro/install/) |
| `openssl`, `python3`, `curl` | helper scripts | normally preinstalled |

macOS shortcut: `brew install k3d kubectl helm`, plus Docker Desktop. For the chat you also want a free AI key from
[Groq](https://console.groq.com); everything else works without it.

### 2. Run one command

```bash
git clone https://github.com/Arnavbits26/urumi-mcp-platform.git
cd urumi-mcp-platform
./scripts/local-up.sh
```

The script checks your tools first, then sets everything up and prints the addresses at the end. It asks **once** for
the AI key (a hidden prompt; press Enter to skip, or use `./scripts/local-up.sh --no-chat`). The first run is slow, mostly
image builds and the WordPress install; each step prints a heading so you can see where it is. It is **safe to run again**.

### 3. Open it

| What | Address |
|---|---|
| Storefront | http://store.localtest.me:8080 |
| WordPress admin | http://store.localtest.me:8080/wp-admin (user `admin`) |
| Dashboard | http://gateway.localtest.me:8080 (sign in as `admin@example.com`) |
| MCP endpoint | http://gateway.localtest.me:8080/mcp |

`*.localtest.me` resolves to 127.0.0.1 through public DNS and k3d forwards port 8080 to the cluster, so there is
nothing to add to `/etc/hosts`. Passwords are random and live only in Kubernetes Secrets; print one when you need it:

```bash
kubectl -n store   get secret store-secrets   -o jsonpath='{.data.wp-admin-password}' | base64 -d; echo
kubectl -n gateway get secret gateway-secrets -o jsonpath='{.data.bootstrap-admin-password}' | base64 -d; echo
```

**It worked if** the dashboard's **Servers** page shows `woocommerce`, `notes` and `utils` as **Connected**.

<details>
<summary><b>Prefer to run the steps yourself?</b> This is exactly what <code>local-up.sh</code> does.</summary>

Run from the repository root, in this order. Every step is safe to repeat.

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

# 4. Gateway and dashboard. llm-secret.sh asks for your AI key with a hidden prompt (optional).
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

</details>

---

## Try it

### Place an order

1. Open the storefront and add a product to the cart. (No products yet? In the WordPress admin choose Products, Add
   New, or ask the chat to create one.)
2. Check out with **Cash on delivery**.
3. In the WordPress admin open **WooCommerce, then Orders**. Your order is listed.

### Use the chat

Sign in to the dashboard and open **Chat**. Ask, for example, *"What orders came in during the last 7 days?"* or
*"Create a product called Mug priced at 299"*. Under each answer the chat lists the tools it used, with their
arguments and results. It acts **as you**: its calls appear in the **Audit log** under your name, tagged "via chat".

### Connect an external MCP client

1. In the dashboard open **API keys** and create your own key. It is shown once.
2. Add the gateway to your client. For Claude Code:

   ```bash
   claude mcp add --transport http urumi http://gateway.localtest.me:8080/mcp \
     --header "Authorization: Bearer YOUR_KEY"
   claude mcp list        # shows urumi as connected
   ```

3. Start the client. You see the tools of **every enabled server** (`woocommerce__...`, `notes__...`, `utils__...`)
   without configuring any of them. Disabling a server in the dashboard removes its tools.

Cursor, MCP Inspector and a free standalone script that needs no paid account are in
[`docs/external-client.md`](docs/external-client.md).

### Add a teammate (admins)

On the **Team** page an admin invites a person, who then creates their own API key. Members use the tools but never see
an upstream credential. An admin can revoke a key at any time; the next call with it is rejected.

The full demo of the assignment's definition of done, with two users, is in [`docs/demo-runbook.md`](docs/demo-runbook.md).

---

## Set it up your way

| I want to... | Do this |
|---|---|
| Try it without an AI key | `./scripts/local-up.sh --no-chat`. The chat shows a setup note until you add one. |
| Add or change the AI key later | `./scripts/llm-secret.sh` (hidden prompt; it restarts the gateway for you) |
| Use another AI provider or model | Any OpenAI-compatible service: set `llm.baseUrl` and `llm.model` in `deploy/values-gateway-local.yaml`, store its key with `./scripts/llm-secret.sh`, then run `./scripts/local-up.sh` |
| Update after pulling new code | `./scripts/local-up.sh` again. It rebuilds the images and restarts the services that use them. |
| Start from scratch | `./scripts/local-down.sh`, then `./scripts/local-up.sh` |
| Decide what members may do | Dashboard, **Permissions**. Blocked tools disappear from the tool list and are refused if called. |
| Let the AI search for tools instead of seeing them all | Dashboard chat, **Tools given to the AI**, or the Helm values `chat.toolMode` and `chat.compactAbove` |
| Run more than one gateway replica | The command below |
| Change the request limit (120 a minute per key) | `settings.mcpRateLimitPerMinute` |

Helm values can be changed on the command line. This sets two and is not remembered by the next upgrade, so put a
change you want to keep in the values file:

```bash
helm upgrade gateway charts/gateway -n gateway -f deploy/values-gateway-local.yaml \
  --set replicaCount=2 --set settings.rateLimitStore=postgres --wait --timeout 5m
```

Details, commands and measurements for permissions, search-first tools, replicas, rate limits and metrics:
[`docs/stand-out-features.md`](docs/stand-out-features.md).

---

## Run it on a server (k3s)

The same charts and scripts run on a fresh Ubuntu server with **only values changed**. On a Mac, a free Multipass VM
acts as the server (about 4 GB of memory is enough). On a real VPS the steps are the same; the guide covers it.

```bash
./scripts/prod/export-images.sh   # build and save versioned images (Docker open)
./scripts/prod/vm-create.sh       # Ubuntu VM with k3s; prints two export lines to run
./scripts/prod/import-images.sh
./scripts/prod/deploy.sh          # all charts, secrets and server registration
```

| What changes | Local | Server |
|---|---|---|
| Addresses | `*.localtest.me:8080` | `store.<ip>.nip.io` and `gateway.<ip>.nip.io` on port 80, or your own domain |
| Values files | `deploy/values-local.yaml`, `values-gateway-local.yaml` | `deploy/values-prod.yaml`, `values-gateway-prod.yaml`, `values-wc-mcp-prod.yaml`, `values-mcp-*-prod.yaml` |
| Host name | in the values file | set by `scripts/prod/deploy.sh` from `BASE_DOMAIN` |
| Images | `urumi/*:dev`, imported with `k3d image import` | `urumi/*:<VERSION>`, built once, saved to a file and imported (or pulled from a registry) |
| Storage | k3d's `local-path` | k3s's `local-path`; on a cloud, its block-storage class |
| Secrets | `scripts/*-secrets.sh` | the **same scripts**, run against the server, so secrets never travel through git |
| TLS | none | cert-manager on a real domain (documented, not exercised) |

**Upgrade and roll back.** Each component is a Helm release with numbered revisions:

```bash
helm history gateway -n gateway          # list revisions
helm rollback gateway 1 -n gateway       # back to a known-good revision (data volumes are kept)
```

`--atomic` makes Helm undo a bad upgrade by itself. I upgraded to an image that does not exist, the old pod kept
serving, and Helm rolled back; the commands are in [`docs/deploy-production.md`](docs/deploy-production.md), which also
has the real-VPS steps, the memory plan and troubleshooting.

---

## How it works

Full notes: [`docs/system-design.md`](docs/system-design.md) and [`docs/threat-model.md`](docs/threat-model.md).

**Design and tradeoffs**

- The gateway is an MCP **server** to clients and an MCP **client** to every upstream, so a person configures one URL
  and one key, not one per server. The dashboard chat and external clients share one `ToolService`, so permissions,
  timeouts and the audit log exist once.
- The store is reached through **WooCommerce's REST API**: authenticated and validated by WooCommerce itself. WP-CLI would
  need exec access to the pod, and direct SQL would bypass WooCommerce's rules.
- **PostgreSQL holds all gateway state**, so any gateway replica can serve any request.
- **Aggregation and routing.** Every tool is exposed as `<server>__<tool>`, so two servers can both have a tool called
  `search`. `tools/list` comes from a cache refreshed every 15 s, with each upstream checked independently, so a slow or
  dead upstream never delays another. Each call opens a short session to one upstream, with the gateway's own
  credential (timeouts: connect 5 s, call 30 s). After 2 failed checks in a row a server is `unreachable`, and it
  reconnects by itself.
- **For production:** DNS names and ports instead of `localtest.me:8080`, a block-storage class and backups, secrets
  created on the server, versioned images from a registry, and TLS.

**Threat model in brief**

- **A shared credential.** Members cannot read it (encrypted, never returned by any API), but any tool they may call
  runs with its full power. Mitigations: audit under their name, instant key revocation, and per-role tool permissions.
- **No token passthrough.** A member's key works only at the gateway. Forwarding it would hand a working key to every
  upstream, including a malicious one (a confused-deputy problem), so each upstream gets its own credential instead.
- **Untrusted upstreams.** A server controls its tool descriptions and could try to steer the model. Descriptions are
  capped and tool output reaches the model only as data; calls, turns and output size are capped; URLs get SSRF checks.
  A model can still be fooled into using a write tool the person is allowed to use.
- **Member to member.** They cannot see each other's keys, audit rows or sessions, but they share tools and data (notes,
  products), and the audit log shows who changed what. Admins can reset passwords, so attribution trusts admins.

---

## Bonus and stand-out items

Details for each: [`docs/stand-out-features.md`](docs/stand-out-features.md).

| # | Item | Status | Where |
|---|---|---|---|
| 1 | Production-like VPS deployment | **Done** on a k3s VM (not public); values-only changes. Public DNS and TLS not exercised | `scripts/prod/`, `deploy/values-*-prod.yaml`, [`docs/deploy-production.md`](docs/deploy-production.md) |
| 2 | Tool permissions | **Done**, per role, default allow; hidden from the list and rejected if called | `app/permissions.py`, the dashboard's Permissions page |
| 3 | OAuth on the gateway | Not attempted | |
| 4 | Handling many tools | **Done**: search, schema and run tools at `/mcp/compact`, and a "Search first" chat mode | `app/compact.py` |
| 5 | Hosted MCP servers | Not attempted | |
| 6 | Security hardening | **Partly**: SSRF guard, credentials encrypted at rest, non-root containers, NetworkPolicies on the three upstreams. Not done: default-deny everywhere, hardened database images | `app/security.py` |
| 7 | Scaling | **Done**: per-key rate limit; the gateway runs as several replicas sharing their counters. One node, one PostgreSQL pod | `app/ratelimit.py`, `app/shared.py` |
| 8 | Observability | **Done, basic**: Prometheus `/metrics`, the audit log, and the reason an upstream is unreachable. No dashboards or alerts | `app/metrics.py` |
| 9 | Upgrades and rollback | **Done**, including a real failed upgrade that Helm rolled back | [`docs/deploy-production.md`](docs/deploy-production.md), section 7 |
| Bonus | Multi-store provisioning | **Not attempted.** How I would build it: [`docs/stand-out-features.md`](docs/stand-out-features.md#not-attempted-and-how-i-would-approach-them) | |

(`app/...` means `services/gateway-backend/app/...`.)

---

## Reference

### Repository map

| Path | What |
|---|---|
| `services/dashboard/` | The dashboard (React, served by nginx) |
| `services/gateway-backend/app/` | The gateway and backend: `api.py`, `mcp_gateway.py`, `toolservice.py`, `registry.py`, `security.py`, and the chat agent in `chat.py` and `llm.py` |
| `services/wc-mcp-server/app/` | The WooCommerce MCP server |
| `services/mcp-notes/`, `services/mcp-utils/` | Two more MCP servers |
| `charts/` | Helm charts: `store`, `wc-mcp`, `gateway`, `demo-mcp` (used twice) |
| `deploy/` | Values files, local and production |
| `scripts/` | `local-up.sh`, `local-down.sh`, secret and image helpers; `scripts/prod/` for the server |
| `docs/` | System design, threat model, production guide, demo runbook, external clients, stand-out features, decisions |

### Tests

They use a real PostgreSQL (from the `pgserver` package), real MCP servers and scripted AI responses. No cluster is needed.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r services/gateway-backend/requirements.txt -r services/wc-mcp-server/requirements.txt \
            -r services/mcp-notes/requirements.txt -r services/mcp-utils/requirements.txt pgserver

(cd services/gateway-backend && for t in test_gateway_e2e test_dashboard_login test_ui_contract test_chat test_demo_upstreams test_external_client test_permissions test_ratelimit_metrics test_many_tools test_scaling; do python -m tests.$t; done)
(cd services/mcp-utils && python -m tests.test_server)
(cd services/mcp-notes && python -m tests.test_server)
(cd services/wc-mcp-server && python -m tests.test_end_to_end)
(cd services/dashboard && npm ci && npm test)
bash scripts/test-local-up-dry-run.sh        # the installer, with stand-in kubectl, helm, k3d and docker
bash scripts/prod/test-deploy-dry-run.sh     # the server deploy script, the same way
```

### Troubleshooting

| Problem | Fix |
|---|---|
| `local-up.sh` says a tool is missing or Docker is not running | Install the tool, or start Docker Desktop, then run it again |
| `local-up.sh` says `KUBECONFIG` is set | That terminal tab points at another cluster. Run `unset KUBECONFIG` or open a new tab |
| The chat says the AI key is missing | `./scripts/llm-secret.sh` |
| A pod is `Pending` or `ImagePullBackOff` | Run `./scripts/local-up.sh` again, then `kubectl get pods -A` |
| The dashboard shows an old page after an update | Hard-refresh the browser |
| A server shows `unreachable` | Press **Check now** on the Servers page, and `kubectl -n mcp get pods` |
| Port 8080 is in use | Stop what uses it, or change the mapping in `scripts/k3d-up.sh` and the addresses in `deploy/values-*local.yaml` |
| The VM's pages do not open from a Mac | Allow your browser under macOS **Local Network** settings; see the production guide |

### Known limits

- Tool permissions are per role and **default-allow**: until an admin sets rules, any member can call any tool.
- The server deployment ran on a local VM, so public DNS and TLS were not exercised.
- PostgreSQL is a single pod, so the gateway scales but the database does not. There are no backups.
- NetworkPolicies protect the three upstreams but are not default-deny everywhere, and the databases and WordPress run
  the vendors' default images.
- Tool definitions are not pinned, so an upstream could change a description later.

The full, prioritised list is at the end of the [threat model](docs/threat-model.md).
