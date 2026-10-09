#!/usr/bin/env bash
# Creates the gateway Secret with random values. Nothing is written to disk or to git.
#   postgres-password   database password
#   encryption-key      Fernet key that encrypts upstream credentials at rest
#   bootstrap-admin-key the first admin's API key (format urm_<8 hex>_<secret>)
set -euo pipefail
NS="${1:-gateway}"
NAME="gateway-secrets"

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

if kubectl -n "$NS" get secret "$NAME" >/dev/null 2>&1; then
  echo "Secret $NAME already exists in namespace $NS (left unchanged)"
  exit 0
fi

rand() { openssl rand -base64 24 | tr -d '/+=\n' | cut -c1-24; }
ENC_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
ADMIN_KEY="urm_$(openssl rand -hex 4)_$(openssl rand -base64 32 | tr -d '/+=\n')"

kubectl -n "$NS" create secret generic "$NAME" \
  --from-literal=postgres-password="$(rand)" \
  --from-literal=encryption-key="$ENC_KEY" \
  --from-literal=bootstrap-admin-key="$ADMIN_KEY"

echo "Created $NAME. Read the admin API key with:"
echo "  kubectl -n $NS get secret $NAME -o jsonpath='{.data.bootstrap-admin-key}' | base64 -d; echo"
