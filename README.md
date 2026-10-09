# urumi-mcp-platform

WooCommerce hosting with a team MCP gateway on Kubernetes (Urumi AI FDE internship, round 1).

Status: **v0.1.0 - store on Kubernetes** (WordPress + WooCommerce + MariaDB, persistent volumes, ingress).

## Local setup (macOS)

Prerequisites: Docker running, plus `k3d`, `kubectl`, `helm`.

```bash
./scripts/k3d-up.sh            # cluster + 'store' namespace
./scripts/store-secrets.sh     # random passwords into a Kubernetes Secret
helm upgrade --install store charts/store -n store -f deploy/values-local.yaml --wait --timeout 10m
```

Open http://store.localtest.me:8080 (storefront) and http://store.localtest.me:8080/wp-admin.
Admin user is `admin`; get the password with the command printed by `store-secrets.sh`.

## Verify v0.1.0

1. Add a product in wp-admin (Products -> Add new), then open the storefront.
2. Add it to the cart and check out with Cash on delivery.
3. Confirm the order in WooCommerce -> Orders.
4. `kubectl -n store delete pod -l app.kubernetes.io/name=wordpress` and
   `kubectl -n store delete pod mariadb-0`; after they restart, the product and order are still there.

## Layout

See `docs/decisions/` for the reasoning behind each choice.
