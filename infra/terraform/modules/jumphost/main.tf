data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  # Match the standard x86_64 platform image, excluding aarch64/Minimal/GPU
  # variants. Filtering by shape can return an empty list in regions where the
  # shape is unavailable, so select by name and let the instance pick the shape.
  filter {
    name   = "display_name"
    values = ["^Canonical-Ubuntu-22\\.04-[0-9]{4}\\."]
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
    source_id   = data.oci_core_images.ubuntu.images[0].id
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
  }
}
