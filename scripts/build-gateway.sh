#!/usr/bin/env bash
# Build the gateway image and load it into the k3d cluster (no registry needed locally).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -t urumi/gateway:dev "$ROOT/services/gateway-backend"
k3d image import urumi/gateway:dev -c urumi
echo "Image urumi/gateway:dev is available inside the cluster"
