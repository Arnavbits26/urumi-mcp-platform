#!/usr/bin/env bash
# Build the notes and utils images and load them into the k3d cluster.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for name in mcp-notes mcp-utils; do
  docker build -t "urumi/$name:dev" "$ROOT/services/$name"
  k3d image import "urumi/$name:dev" -c urumi
done
echo "Images urumi/mcp-notes:dev and urumi/mcp-utils:dev are available inside the cluster"
