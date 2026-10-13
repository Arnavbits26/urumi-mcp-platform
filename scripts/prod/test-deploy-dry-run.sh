#!/usr/bin/env bash
# A self-test for scripts/prod/deploy.sh. It needs NO cluster: it puts stand-in kubectl, helm and curl first on
# PATH, runs the real deploy script, and checks what it asked them to do (order, flags, files, safety checks).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"; SHIM="$WORK/bin"; STATE="$WORK/state"; LOG="$WORK/calls.log"
mkdir -p "$SHIM" "$STATE"; : > "$LOG"
PASS=0; FAIL=0
check() { if eval "$2"; then echo "PASS $1"; PASS=$((PASS+1)); else echo "FAIL $1"; FAIL=$((FAIL+1)); fi; }

cat > "$SHIM/kubectl" <<'SH'
#!/usr/bin/env bash
echo "kubectl $*" >> "$FAKE_LOG"
args="$*"; ns=""; prev=""
for a in "$@"; do [ "$prev" = "-n" ] && ns="$a"; prev="$a"; done
secret_name() { # the word after "secret" (or "generic")
  local seen=""; for a in "$@"; do
    if [ "$seen" = "secret" ] && [ "$a" != "generic" ]; then echo "$a"; return; fi
    if [ "$seen" = "generic" ]; then echo "$a"; return; fi
    case "$a" in secret) seen=secret ;; generic) [ "$seen" = secret ] && seen=generic ;; esac
  done; }
case "$args" in
  *"config current-context"*) echo "${FAKE_CONTEXT:-urumi-prod}" ;;
  *"apply -f -"*) cat >/dev/null ;;
  *"create secret generic"*) touch "$FAKE_STATE/${ns}_$(secret_name "$@")" ;;
  *"get secret"*)
     f="$FAKE_STATE/${ns}_$(secret_name "$@")"
     if [ -f "$f" ]; then case "$args" in *jsonpath*) printf %s ZmFrZQ== ;; esac; else exit 1; fi ;;
  *"wait --for=condition=complete"*) ;;
  *"logs job/wc-key-job"*) echo "WCKEYS ck_fake cs_fake" ;;
  *) : ;;
esac
exit 0
SH
cat > "$SHIM/helm" <<'SH'
#!/usr/bin/env bash
echo "helm $*" >> "$FAKE_LOG"
exit 0
SH
cat > "$SHIM/curl" <<'SH'
#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
out=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
cat >/dev/null 2>&1 || true
[ -n "$out" ] && echo '{"status":"connected","tools":[1,2,3,4]}' > "$out"
printf 201
SH
chmod +x "$SHIM"/*
export FAKE_LOG="$LOG" FAKE_STATE="$STATE" PATH="$SHIM:$PATH"
touch "$STATE/gateway_llm-credentials"    # pretend the AI key is already stored, so no prompt appears

if command -v timeout >/dev/null 2>&1; then TO="timeout 60"; elif command -v gtimeout >/dev/null 2>&1; then TO="gtimeout 60"; else TO=""; fi
run() { (cd "$ROOT" && $TO "$@" > "$WORK/out.txt" 2>&1 < /dev/null); echo $?; }

echo "--- safety checks"
check "refuses without BASE_DOMAIN" '[ "$(run env -u BASE_DOMAIN ./scripts/prod/deploy.sh)" != 0 ] && grep -q "BASE_DOMAIN" "$WORK/out.txt"'
check "refuses to run against the local k3d cluster" '[ "$(run env BASE_DOMAIN=1.2.3.4.nip.io FAKE_CONTEXT=k3d-urumi ./scripts/prod/deploy.sh)" != 0 ] && grep -q "LOCAL cluster" "$WORK/out.txt"'
check "...and did not call helm at all" '! grep -q "^helm " "$LOG"'

echo "--- a full run"
: > "$LOG"; rm -f "$STATE"/store_* "$STATE"/gateway_gateway-secrets "$STATE"/mcp_* 
CODE="$(run env BASE_DOMAIN=1.2.3.4.nip.io ./scripts/prod/deploy.sh)"
check "completes successfully" '[ "$CODE" = 0 ]'
[ "$CODE" = 0 ] || sed 's/^/    /' "$WORK/out.txt" | tail -15
HELM="$(grep '^helm upgrade --install' "$LOG")"
ORDER="$(echo "$HELM" | awk '{print $4}' | tr '\n' ' ')"
check "installs charts in the right order" '[ "$ORDER" = "store wc-mcp gateway mcp-notes mcp-utils " ]'
check "store gets the production values and the domain" 'echo "$HELM" | grep -q "store charts/store .*-f deploy/values-prod.yaml.*host=store.1.2.3.4.nip.io.*siteUrl=http://store.1.2.3.4.nip.io"'
check "gateway gets its domain and both image tags" 'echo "$HELM" | grep -q "gateway charts/gateway .*host=gateway.1.2.3.4.nip.io.*image.tag=0.8.0.*dashboard.image.tag=0.8.0"'
check "every MCP server uses the version tag" '[ "$(echo "$HELM" | grep -c "image.tag=0.8.0")" = 4 ]'
check "every values file named in a helm call exists" 'for f in $(echo "$HELM" | grep -o "deploy/[A-Za-z0-9._-]*yaml" | sort -u); do [ -f "$ROOT/$f" ] || exit 1; done'
check "every chart named in a helm call exists" 'for c in $(echo "$HELM" | grep -o "charts/[a-z-]*" | sort -u); do [ -f "$ROOT/$c/Chart.yaml" ] || exit 1; done'
check "gateway secrets exist BEFORE the gateway is installed" '[ "$(grep -n "create secret generic gateway-secrets" "$LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n "^helm upgrade --install gateway" "$LOG" | head -1 | cut -d: -f1)" ]'
check "WooCommerce API key is made BEFORE wc-mcp is installed" '[ "$(grep -n "create secret generic wc-api-credentials" "$LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n "^helm upgrade --install wc-mcp" "$LOG" | head -1 | cut -d: -f1)" ]'
check "servers are registered against the production gateway address" 'grep -q "http://gateway.1.2.3.4.nip.io/api/servers" "$LOG"'
check "the final message lists the addresses" 'grep -q "http://store.1.2.3.4.nip.io" "$WORK/out.txt" && grep -q "http://gateway.1.2.3.4.nip.io/mcp" "$WORK/out.txt"'
check "no secret value is ever printed" '! grep -qE "ck_fake|cs_fake|ZmFrZQ" "$WORK/out.txt"'

echo "--- another version"
: > "$LOG"; CODE="$(run env BASE_DOMAIN=1.2.3.4.nip.io VERSION=9.9.9 ./scripts/prod/deploy.sh)"
check "VERSION=9.9.9 changes every image tag" '[ "$CODE" = 0 ] && [ "$(grep "^helm upgrade" "$LOG" | grep -c "image.tag=9.9.9")" = 4 ]'

echo; echo "$PASS passed, $FAIL failed"; rm -rf "$WORK"; [ "$FAIL" = 0 ]
