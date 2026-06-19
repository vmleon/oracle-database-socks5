output "jumphost_public_ip" { value = module.jumphost.jumphost_public_ip }
output "adb_private_endpoint" { value = module.adb.private_endpoint }
output "adb_id" { value = module.adb.adb_id }
output "db_name" { value = module.adb.db_name }
output "bastion_id" {
  value = var.enable_bastion ? module.bastion[0].bastion_id : null
}
