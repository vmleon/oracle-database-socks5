terraform {
  required_providers {
    oci = {
      source = "oracle/oci"
    }
  }
}

variable "compartment_ocid" { type = string }
variable "client_cidr" { type = string }
variable "vcn_cidr" {
  type    = string
  default = "10.0.0.0/16"
}
