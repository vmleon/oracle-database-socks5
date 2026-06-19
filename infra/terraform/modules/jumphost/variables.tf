terraform {
  required_providers {
    oci = {
      source = "oracle/oci"
    }
  }
}

variable "compartment_ocid" { type = string }
variable "tenancy_ocid" { type = string }
variable "region" { type = string }
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

# Cloud-init self-provisioning inputs
variable "ansible_par_url" { type = string }
variable "adb_fqdn" { type = string }
variable "client_cidr" { type = string }
variable "socks_port" {
  type    = number
  default = 1080
}
variable "socks_auth_method" {
  type    = string
  default = "none"
}
variable "socks_debug" {
  type    = number
  default = 0
}
