#!/usr/bin/env bash
# Adds a random dashboard password for the admin to the existing gateway Secret (once).
# Run it BEFORE `helm upgrade`, so the new gateway pod picks it up. The password is only used the first
# time the admin has none; after that, change it in the dashboard.
set -euo pipefail
NS="${1:-gateway}"
NAME="gateway-secrets"

kubectl -n "$NS" get secret "$NAME" >/dev/null   # stops with a clear error if the Secret is missing

if [ -n "$(kubectl -n "$NS" get secret "$NAME" -o jsonpath='{.data.bootstrap-admin-password}')" ]; then
  echo "bootstrap-admin-password already exists in $NAME (left unchanged)"
else
  PASSWORD="$(openssl rand -base64 18 | tr -d '/+=\n' | cut -c1-20)"
  ENCODED="$(printf %s "$PASSWORD" | base64 | tr -d '\n')"
  kubectl -n "$NS" patch secret "$NAME" --type merge -p "{\"data\":{\"bootstrap-admin-password\":\"$ENCODED\"}}" >/dev/null
  echo "Added bootstrap-admin-password to $NAME."
fi
echo "Read the admin sign-in password with:"
echo "  kubectl -n $NS get secret $NAME -o jsonpath='{.data.bootstrap-admin-password}' | base64 -d; echo"
echo "Sign in as admin@example.com (the bootstrapAdminEmail value in the chart)."
