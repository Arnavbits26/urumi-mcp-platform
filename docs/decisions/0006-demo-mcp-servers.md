# 0006 - The notes and utils MCP servers

**Why these two.** The assignment needs at least three upstream servers. Together with WooCommerce they
cover every credential style the gateway supports, and they make cross-server prompts natural:

| Server | Nature | Gateway signs in with |
|---|---|---|
| woocommerce | shop data, stateful (WordPress) | nothing (cluster-internal and network-locked) |
| notes | stateful, SQLite on a volume | bearer token |
| utils | stateless, no network calls | custom header `X-Api-Key` |

"Add up my orders from the last 7 days and save the total as a note" needs all three.

**Credential flow.** A random credential is created as a Kubernetes Secret in the `mcp` namespace and
injected into the server as an environment variable. An admin gives the same value to the gateway
(dashboard form or `scripts/register-demo-servers.sh`); the gateway stores it encrypted and presents it
on every upstream call. Team members and their AI clients only ever hold their own gateway key, and the
gateway never forwards that key upstream. Result: a member cannot call notes or utils directly, even if
they could reach them on the network.

**Fail closed.** Both servers refuse to start without their credential, accept only the exact header they
expect (a bearer token is not accepted where `X-Api-Key` is required, and the other way round), compare in
constant time, and leave only `/healthz` open for Kubernetes probes.

**Network lock.** Each server has a ClusterIP Service (no Ingress) and a NetworkPolicy that admits only the
gateway pods. Credential and network policy are two independent locks.

**Calculator safety.** `calculate` never uses `eval`. The expression is parsed to a syntax tree and each
node must be on a short allow-list (numbers, `+ - * / // % **`, brackets, `round abs min max`). Length,
nesting depth, exponent size and result size are capped, so `9 ** 9 ** 9` or a long chain of powers cannot
exhaust memory or CPU.

**Notes are team-shared.** The notes server sees only the gateway's credential, not which person asked, so
any member can read or delete any note. This is a deliberate, documented trade-off (see the threat model).
Stronger options: per-tool permissions so members cannot delete, or forwarding a signed identity header.

**Storage.** `notes.db` lives on a PersistentVolume (`ReadWriteOnce`, so the Deployment uses the
`Recreate` strategy). The claim carries `helm.sh/resource-policy: keep`, so uninstalling the release never
deletes data. One chart (`charts/demo-mcp`) serves both servers; only the values files differ.

**Limits.** One replica each. Tool descriptions are short on purpose: the 8 tools cost about 700 tokens
per model request, which matters on free AI tiers.
