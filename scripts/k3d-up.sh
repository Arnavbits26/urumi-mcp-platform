#!/usr/bin/env bash
# Create the local cluster. Ports: http://*.localtest.me:8080 -> cluster ingress (Traefik).
set -euo pipefail

if k3d cluster list | grep -q '^urumi'; then
  echo "Cluster 'urumi' already exists"
else
  k3d cluster create urumi \
    --agents 0 \
    -p "8080:80@loadbalancer" \
    -p "8443:443@loadbalancer" \
    --wait
fi

kubectl config use-context k3d-urumi
kubectl create namespace store --dry-run=client -o yaml | kubectl apply -f -
kubectl get nodes
