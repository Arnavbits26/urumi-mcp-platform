# Demo runbook: the "definition of done" flow

Prerequisites: the cluster is up, all pods are running, `./scripts/register-demo-servers.sh` has been run,
and two people exist: **user A** (the admin) and **user B** (invite one under Team, copy their key).
Use two browser windows (one private) so both stay signed in.

## 1. Store
1. Open `http://store.localtest.me:8080/shop/`, add a product to the cart, check out with Cash on delivery.
2. In `/wp-admin` open WooCommerce, Orders, and show the order.

## 2. Chat as user A (admin)
3. Chat: `Create a product called Demo mug priced at 299`. Reload `/shop/` to show it.
4. Place an order for it on the storefront by hand.
5. Chat: `What orders came in today?` It returns that order. Open Show details to show the tool call.

## 3. External client as user B
6. Connect a client with only the gateway URL and B's key. Claude Code:
   `claude mcp add --transport http urumi http://gateway.localtest.me:8080/mcp --header "Authorization: Bearer B_KEY"`
   (or Cursor's `mcp.json`, or `npx @modelcontextprotocol/inspector`).
7. List the tools: 13 tools, prefixed `woocommerce__`, `notes__`, `utils__`.
8. Ask the same orders question.
9. A prompt that needs two servers: `Add up the totals of my orders from the last 7 days and save the total as a note called Weekly revenue.`

## 4. Team and control
10. Revoke B's key (Team, Keys, Revoke). Repeat a call from the client: it is rejected (401).
11. Servers, Disable the Utilities server. Its tools vanish from the chat and from the client. Enable it again.
12. Kill an upstream. A plain `kubectl -n mcp delete pod -l app.kubernetes.io/name=mcp-notes` heals in seconds
    (worth showing). To hold it down: `kubectl -n mcp scale deployment/mcp-notes --replicas=0`, then press
    Check now twice on the Servers page (or wait about 30 s): it shows Unreachable while WooCommerce and
    Utilities keep answering. Restore with `kubectl -n mcp scale deployment/mcp-notes --replicas=1`.
13. Audit log: every call above, attributed to the right person, chat calls marked "via chat".

## Kubernetes tour for the video
    kubectl get ns
    kubectl get pods -A
    kubectl get pvc -A
    kubectl get ingress -A
    kubectl get networkpolicy -A
    kubectl -n gateway get secret                  # names only; values are never shown
    kubectl -n gateway describe deploy gateway-backend | grep -A6 -E "Limits|Liveness|Readiness"
Public: the storefront and the gateway/dashboard host (through Ingress). Internal only: WooCommerce MCP,
notes, utils and both databases (ClusterIP, plus NetworkPolicies that admit only the gateway).
