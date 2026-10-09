#!/usr/bin/env bash
# Build the MCP server image and load it into the k3d cluster (no registry needed locally).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -t urumi/wc-mcp:dev "$ROOT/services/wc-mcp-server"
k3d image import urumi/wc-mcp:dev -c urumi
echo "Image urumi/wc-mcp:dev is available inside the cluster"
