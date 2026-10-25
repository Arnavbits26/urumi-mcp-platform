# Production-like deployment (k3s on a VM or a VPS)

The goal: show that the **same Helm charts and the same scripts** run on a fresh server, with only values changed.
Here the server is an Ubuntu VM made with Multipass on a Mac (free, no account). A VPS works the same way; see
"Using a real VPS" at the end.

What this proves: the charts are portable, images are versioned and imported, the domain, ingress port, storage
and sizes come from values, and secrets are created on the server by scripts. What it does not prove: public
internet reachability, real DNS or a trusted TLS certificate (a VM on your laptop is not public).

## What changes between local and production

| | Local (k3d on Docker) | Production-like (k3s on a VM or VPS) |
|---|---|---|
| Cluster | `./scripts/k3d-up.sh` | `./scripts/prod/vm-create.sh` (k3s installed by cloud-init) |
| Domain | `*.localtest.me:8080` | `store.<ip>.nip.io`, `gateway.<ip>.nip.io` on port 80 (a real domain on a VPS) |
| Values files | `deploy/values-local.yaml`, `values-gateway-local.yaml` | `deploy/values-prod.yaml`, `values-gateway-prod.yaml`, `values-wc-mcp-prod.yaml`, `values-mcp-*-prod.yaml` |
| Domain set by | values files | `scripts/prod/deploy.sh` (`--set host=...`, from `BASE_DOMAIN`) |
| Images | `urumi/*:dev`, built and imported by `k3d image import` | `urumi/*:<VERSION>`, built once, saved to a file, imported into k3s |
| Storage | k3d's `local-path` | k3s's `local-path` (set explicitly; on a cloud, its block-storage class) |
| NetworkPolicy on wc-mcp | an extra values file (`values-wc-mcp-netpol.yaml`) | on by default in `values-wc-mcp-prod.yaml` |
| Secrets | `scripts/*-secrets.sh` | the **same scripts**, run against the server |
| TLS and `cookieSecure` | off (plain http) | off on the VM; on for a real domain (see the end) |

The charts themselves are identical in both cases.

## Memory plan for an 8 GB Mac

The whole platform uses about 2 GB inside Kubernetes, so the VM gets 4 GB. macOS plus a 4 GB VM leaves no room for
Docker Desktop, so the order is: **build while Docker is open, quit Docker, then create the VM.**

## 1. Install Multipass and test it (about 5 minutes)

```bash
brew install --cask multipass        # or download the installer from multipass.run
multipass version
multipass launch --name smoke --memory 1G --disk 5G
multipass exec smoke -- uname -m     # expect aarch64
multipass info smoke | grep -i ipv4  # note the address, for example 192.168.64.7
ping -c 2 192.168.64.7               # use your address; the Mac must be able to reach it
multipass delete smoke --purge
```

If any step fails, stop and report it; do not continue.

## 2. Build the images (Docker open)

```bash
./scripts/prod/export-images.sh      # builds urumi/*:0.9.0 and saves dist/urumi-images-0.9.0.tar
```

## 3. Quit Docker Desktop

Stop the local cluster first if you like (`k3d cluster stop urumi`), then quit Docker Desktop from its menu.
Reopen it later to go back to the local setup.

## 4. Create the server

```bash
./scripts/prod/vm-create.sh          # VM + k3s + ./kubeconfig-prod.yaml; prints the next two commands
```

Run the two `export` lines it prints. Do this in **every terminal** you use for production. They point kubectl and
helm at the VM, and `deploy.sh` refuses to run if you are still pointed at the local cluster.

## 5. Load the images, then deploy

```bash
./scripts/prod/import-images.sh
./scripts/prod/deploy.sh             # asks once for your AI key (hidden); everything else is automatic
```

A first run takes 10 to 20 minutes: images are pulled, WordPress and WooCommerce install themselves, and databases start.

## 6. Check it

```bash
kubectl get pods -A                  # everything Running
kubectl top nodes                    # the real memory use on the 4 GB server
kubectl get ingress -A
```

Open the addresses `deploy.sh` printed. Then repeat the demo from `docs/demo-runbook.md` against the production
address. For the external-client script use `URUMI_URL=http://gateway.$BASE_DOMAIN/mcp`.

## 7. Upgrade and roll back with Helm

```bash
helm history gateway -n gateway                  # revisions of the release
```

A normal upgrade is a new image version: `VERSION=0.9.1 ./scripts/prod/export-images.sh`, import, then
`VERSION=0.9.1 ./scripts/prod/deploy.sh`. Data volumes stay.

To show a **safe failure**, upgrade to an image that does not exist. `--atomic` makes Helm undo the upgrade itself:

```bash
# Stops by itself if BASE_DOMAIN is empty in this terminal tab (an empty value would change the site's address).
if [ -z "$BASE_DOMAIN" ]; then
  echo "STOP: BASE_DOMAIN is empty. Run the export line first."
else
  helm upgrade gateway charts/gateway -n gateway -f deploy/values-gateway-prod.yaml \
    --set host=gateway.$BASE_DOMAIN --set image.tag=0.0.0-bad --set dashboard.image.tag=0.9.0 \
    --atomic --timeout 2m
fi
kubectl -n gateway get pods          # the old pod kept serving the whole time
helm history gateway -n gateway      # the failed revision, then the automatic rollback
```

Or roll back by hand to any revision: `helm rollback gateway 1 -n gateway`.

## Using a real VPS

1. A fresh Ubuntu 24.04 server (at least 2 CPUs and 4 GB), reachable by SSH.
2. Install k3s on it: `curl -sfL https://get.k3s.io | sh -`.
3. Copy `/etc/rancher/k3s/k3s.yaml` to your machine as `kubeconfig-prod.yaml`, replacing `127.0.0.1` with the
   server's address (as `vm-create.sh` does), then `export KUBECONFIG=...`.
4. Images: build for the server's CPU type (`docker build --platform linux/amd64 ...` for most VPSs), then
   `SSH_TARGET=ubuntu@<ip> ./scripts/prod/import-images.sh`, or push to a registry and set `image.repository`.
5. Create DNS records `store.<your-domain>` and `gateway.<your-domain>` pointing at the server, then
   `BASE_DOMAIN=<your-domain> ./scripts/prod/deploy.sh`.
6. TLS (optional, **not exercised in this project**): install cert-manager, create a Let's Encrypt
   `ClusterIssuer`, enable `ingress.tls` in the store and gateway values with the issuer annotation, and set
   `cookieSecure: true` and an `https://` `siteUrl`.

## Tips from the first real run

- **Every terminal tab needs the two `export` lines** (`KUBECONFIG` and `BASE_DOMAIN`). A new tab has neither, and a
  `helm upgrade` run without `BASE_DOMAIN` would change the gateway's address to `gateway.`.
- **Chrome says `ERR_ADDRESS_UNREACHABLE` although `curl` works.** macOS blocks apps from the local network until you allow
  them: System Settings, Privacy & Security, Local Network, switch your browser on, then quit and reopen it.
- **The chat says the AI service rejected the API key.** The new cluster starts with no key; if the pasted one was wrong,
  create a new one at your AI service and run `./scripts/llm-secret.sh` (it restarts the gateway).
- **A page can answer 404 for a few seconds during an upgrade** while the ingress is updated. Wait and reload.
- **The VM pauses when the Mac sleeps.** Keep it awake while working, and use `multipass list` and `multipass start urumi-prod` if it stopped.
- **On an 8 GB Mac, keep Docker Desktop closed while the VM runs.** To go back to the local setup:
  `multipass stop urumi-prod`, open Docker Desktop, `k3d cluster start urumi`, and use a new terminal tab without the `export` lines.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ImagePullBackOff` on a urumi image | The images were not imported, or `VERSION` differs. Run `import-images.sh` with the same `VERSION`. |
| Pods stuck `Pending` | Not enough memory: `kubectl describe node`. Stop the VM and give it more: `multipass set local.urumi-prod.memory=5G`. |
| The `.nip.io` names do not resolve | nip.io is down or blocked. Add `<ip> store.<ip>.nip.io gateway.<ip>.nip.io` to `/etc/hosts`. |
| The VM's address changed after a reboot | Re-run `vm-create.sh`, export the new `BASE_DOMAIN`, then `deploy.sh` again. |
| `deploy.sh` says it refuses to run | Your terminal still points at the local cluster: run the two `export` lines. |
| Chrome: `ERR_ADDRESS_UNREACHABLE` | Allow your browser under System Settings, Privacy & Security, Local Network, then restart it. |
| Chat: "The AI service rejected the API key" | Run `./scripts/llm-secret.sh` with a valid key. |
| A page shows 404 right after an upgrade | Normal for a few seconds. If it lasts, run `kubectl -n gateway get ingress` and check the host is `gateway.<BASE_DOMAIN>`. |

## Removing it

`./scripts/prod/vm-destroy.sh` deletes the VM and all its data.
