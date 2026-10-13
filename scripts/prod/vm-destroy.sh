#!/usr/bin/env bash
# Deletes the VM and everything in it (all production data). Asks first.
set -euo pipefail
NAME="${VM_NAME:-urumi-prod}"
read -r -p "Delete the VM '$NAME' and ALL its data? Type yes to continue: " ANSWER
[ "$ANSWER" = "yes" ] || { echo "Nothing changed."; exit 0; }
multipass delete "$NAME" --purge
rm -f "$(cd "$(dirname "$0")/../.." && pwd)/kubeconfig-prod.yaml"
echo "Deleted $NAME."
