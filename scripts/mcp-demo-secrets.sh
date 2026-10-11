#!/usr/bin/env bash
# Creates the credentials the notes and utils servers demand, as Kubernetes Secrets (random values).
# The gateway is later given the same values and stores them encrypted. Nothing is written to disk.
set -euo pipefail
NS="${1:-mcp}"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

make_secret() {   # name key
  if kubectl -n "$NS" get secret "$1" >/dev/null 2>&1; then
    echo "Secret $1 already exists (left unchanged)"
  else
    kubectl -n "$NS" create secret generic "$1" --from-literal="$2"="$(openssl rand -base64 33 | tr -d '/+=\n')" >/dev/null
    echo "Created secret $1"
  fi
}
make_secret notes-credentials token
make_secret utils-credentials api-key
