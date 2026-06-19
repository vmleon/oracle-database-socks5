# Package the Ansible socks5 role and publish it to Object Storage with a
# time-limited pre-authenticated request (PAR). The jump host downloads it via
# the PAR in cloud-init and runs it locally — no SSH push, no inbound access
# needed for provisioning.

data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.compartment_ocid
}

data "archive_file" "ansible" {
  type        = "zip"
  source_dir  = "${path.module}/../../ansible"
  output_path = "${path.module}/generated/ansible.zip"
}

resource "oci_objectstorage_bucket" "artifacts" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  name           = "socks5-poc-artifacts"
  access_type    = "NoPublicAccess"
}

resource "oci_objectstorage_object" "ansible" {
  bucket      = oci_objectstorage_bucket.artifacts.name
  namespace   = data.oci_objectstorage_namespace.ns.namespace
  object      = "ansible.zip"
  source      = data.archive_file.ansible.output_path
  content_md5 = data.archive_file.ansible.output_md5
}

resource "time_static" "deploy" {}

resource "oci_objectstorage_preauthrequest" "ansible" {
  namespace    = data.oci_objectstorage_namespace.ns.namespace
  bucket       = oci_objectstorage_bucket.artifacts.name
  name         = "socks5-poc-ansible-par"
  access_type  = "ObjectRead"
  object_name  = oci_objectstorage_object.ansible.object
  time_expires = timeadd(time_static.deploy.rfc3339, "168h")
}
