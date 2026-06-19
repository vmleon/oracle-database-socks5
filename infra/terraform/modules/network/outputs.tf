output "vcn_id" { value = oci_core_vcn.this.id }
output "public_subnet_id" { value = oci_core_subnet.public.id }
output "private_subnet_id" { value = oci_core_subnet.private.id }
output "jumphost_nsg_id" { value = oci_core_network_security_group.jumphost.id }
output "adb_nsg_id" { value = oci_core_network_security_group.adb.id }
