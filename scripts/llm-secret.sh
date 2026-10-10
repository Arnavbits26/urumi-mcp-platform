#!/usr/bin/env bash
# Stores your AI service API key in a Kubernetes Secret.
# The key is typed at a hidden prompt, so it never appears on screen, in your shell history, in a
# file, or in a Helm value. Run it again any time to replace the key.
set -euo pipefail
NS="${1:-gateway}"

read -r -s -p "Paste your AI service API key (input is hidden): " KEY
echo
if [ -z "$KEY" ]; then
  echo "No key entered. Nothing changed."
  exit 1
fi
case "$KEY" in
  gsk_*) ;;
  *) echo "Note: Groq keys start with gsk_. Continuing anyway." ;;
esac

kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# The key goes in through a pipe, not on the command line, so other programs cannot see it in `ps`.
printf %s "$KEY" | kubectl -n "$NS" create secret generic llm-credentials \
  --from-file=api-key=/dev/stdin --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset KEY
echo "Saved the key in the Secret llm-credentials (namespace $NS)."

# Pods read Secrets when they start, so restart the gateway if it is already running.
if kubectl -n "$NS" get deployment gateway-backend >/dev/null 2>&1; then
  kubectl -n "$NS" rollout restart deployment/gateway-backend >/dev/null
  kubectl -n "$NS" rollout status deployment/gateway-backend --timeout=120s
fi
echo "Done. Open the Chat page and ask a question."
