resource "oci_bastion_bastion" "this" {
  compartment_id               = var.compartment_ocid
  bastion_type                 = "STANDARD"
  target_subnet_id             = var.private_subnet_id
  name                         = "socks5pocbastion"
  client_cidr_block_allow_list = [var.client_cidr]
}
