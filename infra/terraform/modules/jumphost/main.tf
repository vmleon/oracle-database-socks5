data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ol9" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  # Match the standard x86_64 Oracle Linux 9 platform image, excluding aarch64,
  # GPU, and other variants. Filtering by shape can return an empty list in
  # regions where the shape is unavailable, so select by name and let the
  # instance pick the shape.
  filter {
    name   = "display_name"
    values = ["^Oracle-Linux-9\\.[0-9]+-[0-9]{4}\\.[0-9]{2}\\.[0-9]{2}-[0-9]+$"]
    regex  = true
  }
}

resource "oci_core_instance" "jumphost" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "socks5-poc-jumphost"
  shape               = var.jumphost_shape

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_in_gbs
  }

  create_vnic_details {
    subnet_id        = var.public_subnet_id
    assign_public_ip = true
    nsg_ids          = [var.jumphost_nsg_id]
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ol9.images[0].id
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/userdata/bootstrap.tftpl", {
      region            = var.region
      ansible_par_url   = var.ansible_par_url
      adb_fqdn          = var.adb_fqdn
      client_cidr       = var.client_cidr
      socks_port        = var.socks_port
      socks_auth_method = var.socks_auth_method
      socks_debug       = var.socks_debug
    }))
  }
}
