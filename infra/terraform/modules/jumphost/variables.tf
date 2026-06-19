terraform {
  required_providers {
    oci = {
      source = "oracle/oci"
    }
  }
}

variable "compartment_ocid" { type = string }
variable "tenancy_ocid" { type = string }
variable "public_subnet_id" { type = string }
variable "jumphost_nsg_id" { type = string }
variable "ssh_public_key" { type = string }
variable "jumphost_shape" { type = string }
variable "ocpus" {
  type    = number
  default = 1
}
variable "memory_in_gbs" {
  type    = number
  default = 8
}
