resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "socks5-poc-vcn"
  dns_label      = "socks5poc"
}

resource "oci_core_default_security_list" "default" {
  manage_default_resource_id = oci_core_vcn.this.default_security_list_id
  compartment_id             = var.compartment_ocid
  display_name               = "socks5-poc-default-sl"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }
  # no ingress rules: all ingress is governed by NSGs (client-CIDR allowlist)
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-igw"
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-public-rt"
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = "10.0.1.0/24"
  display_name               = "socks5-poc-public-subnet"
  route_table_id             = oci_core_route_table.public.id
  prohibit_public_ip_on_vnic = false
  dns_label                  = "pub"
}

resource "oci_core_subnet" "private" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = "10.0.2.0/24"
  display_name               = "socks5-poc-private-subnet"
  prohibit_public_ip_on_vnic = true
  dns_label                  = "priv"
}

# NSG for the jump host: ingress 22 + 1080 from client only; egress 1522 to ADB NSG
resource "oci_core_network_security_group" "jumphost" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-jumphost-nsg"
}

# NSG for ADB private endpoint: ingress 1522 from jumphost NSG only.
# depends_on orders destroy after the private subnet: the subnet blocks until the
# ADB private-endpoint VNIC fully detaches, so by the time the NSG is deleted the
# VNIC is gone (otherwise the NSG delete races the lingering VNIC and 412s).
resource "oci_core_network_security_group" "adb" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "socks5-poc-adb-nsg"

  depends_on = [oci_core_subnet.private]
}

resource "oci_core_network_security_group_security_rule" "jh_ssh" {
  network_security_group_id = oci_core_network_security_group.jumphost.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.client_cidr
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "jh_socks" {
  network_security_group_id = oci_core_network_security_group.jumphost.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.client_cidr
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 1080
      max = 1080
    }
  }
}

resource "oci_core_network_security_group_security_rule" "jh_egress_adb" {
  network_security_group_id = oci_core_network_security_group.jumphost.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.adb.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 1522
      max = 1522
    }
  }
}

resource "oci_core_network_security_group_security_rule" "adb_ingress_jh" {
  network_security_group_id = oci_core_network_security_group.adb.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.jumphost.id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = 1522
      max = 1522
    }
  }
}
