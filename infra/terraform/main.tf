module "network" {
  source           = "./modules/network"
  compartment_ocid = var.compartment_ocid
  client_cidr      = var.client_cidr
}

module "adb" {
  source            = "./modules/adb"
  compartment_ocid  = var.compartment_ocid
  db_name           = var.db_name
  db_version        = var.db_version
  db_admin_password = var.db_admin_password
  adb_ecpu_count    = var.adb_ecpu_count
  private_subnet_id = module.network.private_subnet_id
  adb_nsg_id        = module.network.adb_nsg_id
}

module "jumphost" {
  source           = "./modules/jumphost"
  compartment_ocid = var.compartment_ocid
  tenancy_ocid     = var.tenancy_ocid
  region           = var.region
  public_subnet_id = module.network.public_subnet_id
  jumphost_nsg_id  = module.network.jumphost_nsg_id
  ssh_public_key   = var.ssh_public_key
  jumphost_shape   = var.jumphost_shape

  ansible_par_url = oci_objectstorage_preauthrequest.ansible.full_path
  adb_fqdn        = module.adb.private_endpoint
  client_cidr     = var.client_cidr
}

module "bastion" {
  count             = var.enable_bastion ? 1 : 0
  source            = "./modules/bastion"
  compartment_ocid  = var.compartment_ocid
  private_subnet_id = module.network.private_subnet_id
  client_cidr       = var.client_cidr
}
