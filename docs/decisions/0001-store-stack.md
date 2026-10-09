# 0001 - Store stack and local setup

**Cluster:** k3d (k3s in Docker). Same distribution as the production VPS, ships Traefik and a
local-path StorageClass, and enforces NetworkPolicies.

**Chart:** our own small `charts/store` chart (official `wordpress` and `mariadb` images) instead of
a third-party chart, so every resource is ours to explain.

**Local domain:** `store.localtest.me` resolves to 127.0.0.1 via public DNS. k3d maps host port 8080
to the cluster ingress, so the URL is `http://store.localtest.me:8080`. No /etc/hosts edits.

**Secrets:** created by `scripts/store-secrets.sh` with random values. The chart only references the
Secret by name, so no secret is in git or in values files.

**Bootstrap:** a Helm post-install/post-upgrade Job runs WP-CLI (`files/setup.sh`) to install
WordPress and WooCommerce, enable cash on delivery and add a shipping method. The script is
idempotent.

**Version pinning:** the latest WooCommerce requires WordPress 7.0+, so the chart uses
`wordpress:7-apache`. The setup Job fails loudly if the plugin cannot be installed.

**Single-node assumption:** the setup Job mounts the same ReadWriteOnce volume as WordPress. That is
fine on one node (k3d, single-VPS k3s); a multi-node cluster would need RWX storage or a sidecar.
