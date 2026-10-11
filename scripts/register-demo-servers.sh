#!/usr/bin/env bash
# Registers the notes and utils servers in the gateway, using the admin API key, exactly as an admin
# would in the dashboard (Servers -> Add server). Each server's credential is read from its Secret and
# sent to the gateway over stdin, so it never appears on screen or in your shell history.
# Safe to run again: a server that already exists is skipped.
set -euo pipefail
BASE="${1:-http://gateway.localtest.me:8080}"
NOTES_URL="${NOTES_URL:-http://mcp-notes.mcp.svc.cluster.local:8000/mcp}"
UTILS_URL="${UTILS_URL:-http://mcp-utils.mcp.svc.cluster.local:8000/mcp}"
ADMIN_KEY="$(kubectl -n gateway get secret gateway-secrets -o jsonpath='{.data.bootstrap-admin-key}' | base64 -d)"

register() {   # slug  name  url  auth_type  header_name  secret_name  secret_key
  local slug="$1" name="$2" url="$3" auth="$4" header="$5" secret="$6" key="$7" out code
  out="$(mktemp)"
  code="$(
    CRED="$(kubectl -n mcp get secret "$secret" -o jsonpath="{.data.$key}" | base64 -d)" \
    python3 -c '
import json, os, sys
slug, name, url, auth, header = sys.argv[1:6]
body = {"slug": slug, "name": name, "url": url, "auth_type": auth, "credential": os.environ["CRED"]}
if header:
    body["auth_header_name"] = header
print(json.dumps(body))' "$slug" "$name" "$url" "$auth" "$header" |
    curl -s -o "$out" -w '%{http_code}' -X POST "$BASE/api/servers" \
      -H "Authorization: Bearer $ADMIN_KEY" -H 'Content-Type: application/json' --data-binary @-
  )"
  case "$code" in
    201) echo "Registered $slug ($(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["status"], "-", len(d["tools"]), "tools")' "$out"))" ;;
    409) echo "$slug is already registered (skipped)" ;;
    *)   echo "Could not register $slug (HTTP $code):"; cat "$out"; echo ;;
  esac
  rm -f "$out"
}

register notes "Notes" "$NOTES_URL" bearer "" notes-credentials token
register utils "Utilities" "$UTILS_URL" header "X-Api-Key" utils-credentials api-key
