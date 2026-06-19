variable "oci_profile" {
  type    = string
  default = "DEFAULT"
}
variable "region" { type = string }
variable "compartment_ocid" { type = string }
variable "client_cidr" {
  type        = string
  description = "Operator/client public egress CIDR (e.g. 203.0.113.10/32)"
}
variable "ssh_public_key" { type = string }
variable "db_version" {
  type    = string
  default = "26ai"
}
variable "db_name" {
  type    = string
  default = "dbpoc"
}
variable "auth_mode" {
  type    = string
  default = "mtls"
  validation {
    condition     = contains(["mtls", "tls"], var.auth_mode)
    error_message = "auth_mode must be mtls or tls."
  }
}
variable "db_admin_password" {
  type      = string
  sensitive = true
}
variable "adb_ecpu_count" {
  type    = number
  default = 2
}
variable "jumphost_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}
variable "enable_bastion" {
  type    = bool
  default = false
}
