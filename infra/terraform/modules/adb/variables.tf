variable "compartment_ocid" { type = string }
variable "db_name" { type = string }
variable "db_version" { type = string }
variable "db_admin_password" {
  type      = string
  sensitive = true
}
variable "adb_ecpu_count" { type = number }
variable "private_subnet_id" { type = string }
variable "adb_nsg_id" { type = string }
