<?php
// Run through `wp eval-file`. Creates one read/write WooCommerce REST API key for the admin user
// (user ID 1) and prints it once on a line starting with WCKEYS. Re-running replaces the old key.
global $wpdb;
$table = $wpdb->prefix . 'woocommerce_api_keys';
$wpdb->delete( $table, array( 'description' => 'urumi-mcp' ) );

$ck = 'ck_' . wc_rand_hash();
$cs = 'cs_' . wc_rand_hash();
$ok = $wpdb->insert(
	$table,
	array(
		'user_id'         => 1,
		'description'     => 'urumi-mcp',
		'permissions'     => 'read_write',
		'consumer_key'    => wc_api_hash( $ck ),
		'consumer_secret' => $cs,
		'truncated_key'   => substr( $ck, -7 ),
	)
);
if ( ! $ok ) {
	fwrite( STDERR, "Failed to insert API key\n" );
	exit( 1 );
}
echo "WCKEYS $ck $cs\n";
