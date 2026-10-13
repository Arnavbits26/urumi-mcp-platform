#!/usr/bin/env bash
# Copies the image file made by export-images.sh to the server and loads it into k3s's container store.
#   Multipass VM (default):  ./scripts/prod/import-images.sh
#   A real VPS over SSH:     SSH_TARGET=ubuntu@203.0.113.10 ./scripts/prod/import-images.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${VERSION:-0.8.0}"
FILE="$ROOT/dist/urumi-images-$VERSION.tar"
NAME="${VM_NAME:-urumi-prod}"
[ -f "$FILE" ] || { echo "$FILE not found. Run scripts/prod/export-images.sh first."; exit 1; }

if [ -n "${SSH_TARGET:-}" ]; then
  scp "$FILE" "$SSH_TARGET:/tmp/urumi-images.tar"
  ssh "$SSH_TARGET" 'sudo k3s ctr images import /tmp/urumi-images.tar && rm -f /tmp/urumi-images.tar && sudo k3s ctr images ls -q | grep "urumi/"'
else
  multipass transfer "$FILE" "$NAME:/tmp/urumi-images.tar"
  multipass exec "$NAME" -- sudo k3s ctr images import /tmp/urumi-images.tar
  multipass exec "$NAME" -- sudo rm -f /tmp/urumi-images.tar
  multipass exec "$NAME" -- sudo k3s ctr images ls -q | grep "urumi/"
fi
