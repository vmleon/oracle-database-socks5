resource "oci_database_autonomous_database" "this" {
  compartment_id           = var.compartment_ocid
  db_name                  = var.db_name
  display_name             = var.db_name
  db_version               = var.db_version
  db_workload              = "OLTP"
  compute_model            = "ECPU"
  compute_count            = var.adb_ecpu_count
  data_storage_size_in_tbs = 1
  admin_password           = var.db_admin_password
  is_auto_scaling_enabled  = false

  # private endpoint
  subnet_id              = var.private_subnet_id
  nsg_ids                = [var.adb_nsg_id]
  private_endpoint_label = "dbpoc-pe"

  # mTLS only (mutual TLS required; not walletless TLS at the listener)
  is_mtls_connection_required = true
}
