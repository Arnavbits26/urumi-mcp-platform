#!/bin/sh
# Idempotent WordPress + WooCommerce bootstrap. Safe to re-run on every helm upgrade.
set -eu

WP_PATH=/var/www/html
wp() { php -d memory_limit=512M /usr/local/bin/wp --path="$WP_PATH" "$@"; }
printf 'apache_modules:\n  - mod_rewrite\n' > /tmp/wp-cli.yml
export WP_CLI_CONFIG_PATH=/tmp/wp-cli.yml

echo "[setup] waiting for wp-config.php (created by the wordpress container)..."
i=0
until [ -f "$WP_PATH/wp-config.php" ]; do
  i=$((i + 1)); [ "$i" -gt 120 ] && { echo "[setup] timed out waiting for wp-config.php"; exit 1; }
  sleep 5
done

echo "[setup] waiting for the database..."
i=0
until php -r '$m=@new mysqli(getenv("DB_HOST"),getenv("DB_USER"),getenv("DB_PASSWORD"),getenv("DB_NAME")); exit($m->connect_errno ? 1 : 0);'; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { echo "[setup] timed out waiting for the database"; exit 1; }
  sleep 5
done

if wp core is-installed 2>/dev/null; then
  echo "[setup] WordPress already installed"
else
  echo "[setup] installing WordPress"
  wp core install --url="$SITE_URL" --title="$SITE_TITLE" \
    --admin_user="$ADMIN_USER" --admin_password="$ADMIN_PASSWORD" \
    --admin_email="$ADMIN_EMAIL" --skip-email
fi

echo "[setup] pretty permalinks (needed for the WooCommerce REST API)"
wp rewrite structure '/%postname%/' --hard

if ! wp plugin is-installed woocommerce; then
  echo "[setup] installing WooCommerce"
  wp plugin install woocommerce
fi
wp plugin activate woocommerce

echo "[setup] configuring WooCommerce"
wp option update woocommerce_currency "$CURRENCY"
wp option update woocommerce_coming_soon no
wp option update woocommerce_store_pages_only no
wp option update woocommerce_cod_settings '{"enabled":"yes","title":"Cash on delivery","description":"Pay with cash upon delivery."}' --format=json
wp eval 'WC_Install::create_pages();'
wp eval '$z = new WC_Shipping_Zone(0); if (!count($z->get_shipping_methods())) { $z->add_shipping_method("free_shipping"); echo "added free shipping\n"; }'

echo "[setup] finishing the WooCommerce install, then applying store defaults"
# A fresh WooCommerce puts the shop in "Coming soon" mode (hidden from visitors) and picks its own currency.
# Run its install routine now, so nothing is left to happen on the first web request, and set our defaults AFTER it.
wp eval 'if (class_exists("WC_Install")) { WC_Install::install(); echo "install routine done\n"; }' || echo "[setup] note: install routine reported an error (continuing)"
wp option update woocommerce_coming_soon no
wp option update woocommerce_store_pages_only no
wp option update woocommerce_currency "$CURRENCY"

# Safety net: a must-use plugin that always answers "not coming soon", whatever else changes the option later.
# (Side effect, by design: the Live / Coming soon switch in wp-admin no longer has an effect on this store.)
mkdir -p "$WP_PATH/wp-content/mu-plugins"
cat > "$WP_PATH/wp-content/mu-plugins/urumi-store-defaults.php" <<'PHP'
<?php
/**
 * Plugin Name: Urumi store defaults
 * Description: Keeps the demo store open to visitors (never in WooCommerce "Coming soon" mode).
 */
add_filter( 'pre_option_woocommerce_coming_soon', function () { return 'no'; } );
PHP

echo "[setup] coming_soon is now: $(wp eval 'echo get_option("woocommerce_coming_soon");')"
echo "[setup] currency is now:    $(wp eval 'echo get_option("woocommerce_currency");')"
echo "[setup] done"
