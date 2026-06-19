# mTLS mode: generate the ADB wallet with Terraform and unzip it into ./wallet
# on the machine running `tf apply` (the client that runs the app). The jump
# host never receives the wallet — it stays a dumb relay. For auth_mode = tls
# (walletless) nothing here is created.

resource "oci_database_autonomous_database_wallet" "this" {
  count                  = var.auth_mode == "mtls" ? 1 : 0
  autonomous_database_id = module.adb.adb_id
  password               = var.db_admin_password
  generate_type          = "SINGLE"
  base64_encode_content  = true
}

resource "local_file" "wallet_zip" {
  count                = var.auth_mode == "mtls" ? 1 : 0
  content_base64       = oci_database_autonomous_database_wallet.this[0].content
  filename             = "${path.module}/../../wallet/wallet.zip"
  file_permission      = "0600"
  directory_permission = "0700"
}

resource "terraform_data" "wallet_unzip" {
  count            = var.auth_mode == "mtls" ? 1 : 0
  triggers_replace = [local_file.wallet_zip[0].content_base64]

  provisioner "local-exec" {
    working_dir = "${path.module}/../.."
    command     = "chmod 700 wallet && chmod 600 wallet/wallet.zip && unzip -o wallet/wallet.zip -d wallet && chmod 600 wallet/*"
  }
}
