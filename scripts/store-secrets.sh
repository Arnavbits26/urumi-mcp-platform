#!/usr/bin/env bash
# Creates the store Secret with random passwords. Values never touch git or Helm values.
set -euo pipefail
NS="${1:-store}"
NAME="store-secrets"

if kubectl -n "$NS" get secret "$NAME" >/dev/null 2>&1; then
  echo "Secret $NAME already exists in namespace $NS (left unchanged)"
  exit 0
fi

rand() { openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-24; }

kubectl -n "$NS" create secret generic "$NAME" \
  --from-literal=mariadb-root-password="$(rand)" \
  --from-literal=mariadb-password="$(rand)" \
  --from-literal=wp-admin-password="$(rand)"

echo "Created $NAME. Read the WordPress admin password with:"
echo "  kubectl -n $NS get secret $NAME -o jsonpath='{.data.wp-admin-password}' | base64 -d; echo"
