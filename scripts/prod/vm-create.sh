#!/usr/bin/env bash
# Creates an Ubuntu VM with Multipass, installs k3s in it (see cloud-init.yaml), and copies the kubeconfig to
# ./kubeconfig-prod.yaml so kubectl and helm on this machine can manage it, as they would manage a VPS.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${VM_NAME:-urumi-prod}"; CPUS="${VM_CPUS:-2}"; MEM="${VM_MEMORY:-4G}"; DISK="${VM_DISK:-20G}"

command -v multipass >/dev/null || { echo "Multipass is not installed. See docs/deploy-production.md, step 1."; exit 1; }

if multipass info "$NAME" >/dev/null 2>&1; then
  echo "The VM '$NAME' already exists; using it."
  multipass start "$NAME" >/dev/null 2>&1 || true
else
  echo "Creating VM '$NAME' ($CPUS CPUs, $MEM memory, $DISK disk). This takes a few minutes..."
  multipass launch 24.04 --name "$NAME" --cpus "$CPUS" --memory "$MEM" --disk "$DISK" \
    --cloud-init "$ROOT/scripts/prod/cloud-init.yaml" --timeout 900
fi

echo "Waiting for Kubernetes to be ready..."
for _ in $(seq 1 60); do
  if multipass exec "$NAME" -- sudo k3s kubectl get nodes 2>/dev/null | grep -q ' Ready'; then break; fi
  sleep 5
done
multipass exec "$NAME" -- sudo k3s kubectl get nodes | grep -q ' Ready' || { echo "k3s did not become ready. Try: multipass exec $NAME -- sudo journalctl -u k3s | tail"; exit 1; }

IP="$(multipass exec "$NAME" -- hostname -I | awk '{print $1}')"
OUT="$ROOT/kubeconfig-prod.yaml"
multipass exec "$NAME" -- sudo cat /etc/rancher/k3s/k3s.yaml | sed -e "s/127.0.0.1/$IP/" -e 's/: default$/: urumi-prod/' > "$OUT"
chmod 600 "$OUT"

echo
echo "VM ready. Address: $IP"
echo
echo "In every terminal where you work with the PRODUCTION cluster, run first:"
echo "  export KUBECONFIG=$OUT"
echo "  export BASE_DOMAIN=$IP.nip.io"
echo
echo "Your site names will be store.$IP.nip.io and gateway.$IP.nip.io (nip.io turns a name that contains an"
echo "address back into that address, so no DNS setup is needed)."
