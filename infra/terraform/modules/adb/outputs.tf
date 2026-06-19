# Note: is_mtls_connection_required = true keeps the mTLS wallet path. For auth_mode = tls,
# the README documents flipping this to false (walletless TLS available because the endpoint
# is private). The PoC primary is mtls; the toggle is documented, not auto-flipped, to keep
# the apply deterministic.

output "adb_id" { value = oci_database_autonomous_database.this.id }
output "private_endpoint" { value = oci_database_autonomous_database.this.private_endpoint }
output "db_name" { value = oci_database_autonomous_database.this.db_name }
