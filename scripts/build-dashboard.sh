#!/usr/bin/env bash
# Build the dashboard image (React app served by nginx) and load it into the k3d cluster.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -t urumi/dashboard:dev "$ROOT/services/dashboard"
k3d image import urumi/dashboard:dev -c urumi
echo "Image urumi/dashboard:dev is available inside the cluster"
