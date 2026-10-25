#!/usr/bin/env bash
# Builds the five service images with a VERSION tag (not "dev") and saves them into one file that can be
# copied to a server. Needs Docker running. Layer caching makes repeat runs fast.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${VERSION:-0.9.3}"
OUT="$ROOT/dist/urumi-images-$VERSION.tar"
mkdir -p "$ROOT/dist"

docker build -t "urumi/gateway:$VERSION"   "$ROOT/services/gateway-backend"
docker build -t "urumi/dashboard:$VERSION" "$ROOT/services/dashboard"
docker build -t "urumi/wc-mcp:$VERSION"    "$ROOT/services/wc-mcp-server"
docker build -t "urumi/mcp-notes:$VERSION" "$ROOT/services/mcp-notes"
docker build -t "urumi/mcp-utils:$VERSION" "$ROOT/services/mcp-utils"

docker save -o "$OUT" "urumi/gateway:$VERSION" "urumi/dashboard:$VERSION" "urumi/wc-mcp:$VERSION" \
                      "urumi/mcp-notes:$VERSION" "urumi/mcp-utils:$VERSION"
ARCH="$(docker image inspect "urumi/gateway:$VERSION" --format '{{.Architecture}}')"
echo "Saved $OUT ($(du -h "$OUT" | cut -f1)), built for $ARCH."
echo "The server must have the same CPU type ($ARCH). A VM on an Apple-silicon Mac is arm64, so it matches."
