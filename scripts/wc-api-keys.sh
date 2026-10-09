#!/usr/bin/env bash
# Creates a WooCommerce REST API key and stores it in the Kubernetes Secret `wc-api-credentials`.
# The key is generated inside the cluster, read once from the Job log, and never written to disk
# or to git. To rotate: kubectl -n store delete secret wc-api-credentials && ./scripts/wc-api-keys.sh
set -euo pipefail
NS="${1:-store}"
SECRET="wc-api-credentials"
DIR="$(cd "$(dirname "$0")" && pwd)"

if kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
  echo "Secret $SECRET already exists in namespace $NS (left unchanged)"
  exit 0
fi

kubectl -n "$NS" delete job wc-key-job --ignore-not-found >/dev/null
kubectl -n "$NS" create configmap wc-key-script \
  --from-file=create-key.php="$DIR/files/create-wc-key.php" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

cat <<YAML | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: wc-key-job
spec:
  backoffLimit: 1
  activeDeadlineSeconds: 300
  template:
    spec:
      restartPolicy: Never
      securityContext: { runAsUser: 33, runAsGroup: 33, fsGroup: 33 }
      containers:
        - name: create-key
          image: wordpress:cli
          command: ["php", "-d", "memory_limit=512M", "/usr/local/bin/wp", "eval-file", "/scripts/create-key.php", "--path=/var/www/html"]
          env:
            - { name: HOME, value: /tmp }
            - { name: WORDPRESS_DB_HOST, value: "mariadb:3306" }
            - { name: WORDPRESS_DB_NAME, value: wordpress }
            - { name: WORDPRESS_DB_USER, value: wordpress }
            - name: WORDPRESS_DB_PASSWORD
              valueFrom: { secretKeyRef: { name: store-secrets, key: mariadb-password } }
          resources:
            requests: { cpu: 50m, memory: 128Mi }
            limits: { cpu: 500m, memory: 768Mi }
          volumeMounts:
            - { name: wordpress-data, mountPath: /var/www/html }
            - { name: script, mountPath: /scripts }
      volumes:
        - name: wordpress-data
          persistentVolumeClaim: { claimName: wordpress-data }
        - name: script
          configMap: { name: wc-key-script }
YAML

echo "Waiting for the key job..."
if ! kubectl -n "$NS" wait --for=condition=complete job/wc-key-job --timeout=180s >/dev/null; then
  echo "Key job failed. Logs:"; kubectl -n "$NS" logs job/wc-key-job || true
  exit 1
fi

LINE="$(kubectl -n "$NS" logs job/wc-key-job | grep '^WCKEYS ' | tail -1)"
CK="$(echo "$LINE" | awk '{print $2}')"
CS="$(echo "$LINE" | awk '{print $3}')"
if [ -z "$CK" ] || [ -z "$CS" ]; then
  echo "Could not read the key from the job log"; exit 1
fi

kubectl -n "$NS" create secret generic "$SECRET" \
  --from-literal=consumer-key="$CK" --from-literal=consumer-secret="$CS"

kubectl -n "$NS" delete job wc-key-job >/dev/null
kubectl -n "$NS" delete configmap wc-key-script >/dev/null
echo "Created secret $SECRET in namespace $NS"
