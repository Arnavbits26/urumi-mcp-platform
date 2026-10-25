#!/usr/bin/env bash
# Deploys the whole platform to a production-like cluster with the SAME charts and scripts as local, changing
# only values. Run it from this machine with KUBECONFIG pointing at the server (vm-create.sh prints how).
#   BASE_DOMAIN=192.168.64.5.nip.io ./scripts/prod/deploy.sh
# Safe to run again. Needs the images to be imported first (import-images.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
BASE="${BASE_DOMAIN:?Set BASE_DOMAIN first, for example: BASE_DOMAIN=192.168.64.5.nip.io}"
VERSION="${VERSION:-0.9.3}"

CONTEXT="$(kubectl config current-context)"
case "$CONTEXT" in
  k3d-*) echo "Refusing to run: kubectl is pointed at '$CONTEXT', your LOCAL cluster."
         echo "Run: export KUBECONFIG=$ROOT/kubeconfig-prod.yaml"; exit 1 ;;
esac
echo "Deploying version $VERSION to '$CONTEXT' at $BASE"

ensure_namespace() { kubectl create namespace "$1" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; }

echo "== store (WordPress, WooCommerce, MariaDB)"
ensure_namespace store
./scripts/store-secrets.sh
helm upgrade --install store charts/store -n store -f deploy/values-prod.yaml \
  --set "host=store.$BASE" --set "siteUrl=http://store.$BASE" --wait --timeout 20m

echo "== WooCommerce MCP server"
./scripts/wc-api-keys.sh
helm upgrade --install wc-mcp charts/wc-mcp -n store -f deploy/values-wc-mcp-prod.yaml \
  --set "image.tag=$VERSION" --wait --timeout 5m

echo "== gateway and dashboard"
ensure_namespace gateway
./scripts/gateway-secrets.sh
./scripts/gateway-admin-password.sh
if ! kubectl -n gateway get secret llm-credentials >/dev/null 2>&1; then ./scripts/llm-secret.sh; fi
helm upgrade --install gateway charts/gateway -n gateway -f deploy/values-gateway-prod.yaml \
  --set "host=gateway.$BASE" --set "image.tag=$VERSION" --set "dashboard.image.tag=$VERSION" --wait --timeout 10m

echo "== notes and utils MCP servers"
./scripts/mcp-demo-secrets.sh
helm upgrade --install mcp-notes charts/demo-mcp -n mcp -f deploy/values-mcp-notes-prod.yaml \
  --set "image.tag=$VERSION" --wait --timeout 5m
helm upgrade --install mcp-utils charts/demo-mcp -n mcp -f deploy/values-mcp-utils-prod.yaml \
  --set "image.tag=$VERSION" --wait --timeout 5m
./scripts/register-demo-servers.sh "http://gateway.$BASE"

cat <<DONE

Done. Open:
  Storefront      http://store.$BASE
  WordPress admin http://store.$BASE/wp-admin   (user: admin)
  Dashboard       http://gateway.$BASE          (sign in as admin@example.com)
  MCP endpoint    http://gateway.$BASE/mcp

Passwords (printed only when you ask):
  kubectl -n store   get secret store-secrets   -o jsonpath='{.data.wp-admin-password}' | base64 -d; echo
  kubectl -n gateway get secret gateway-secrets -o jsonpath='{.data.bootstrap-admin-password}' | base64 -d; echo
DONE
