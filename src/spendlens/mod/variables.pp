// Values are normally supplied by spendlens from finops.yaml (`spendlens params`).
// The mod can also be run directly: powerpipe benchmark run spendlens.benchmark.tags --mod-location <this dir>

variable "database" {
  type        = connection.steampipe
  description = "Steampipe connection. spendlens points this at a generated connection.steampipe.spendlens."
  default     = connection.steampipe.default
}

variable "common_dimensions" {
  type        = list(string)
  description = "Common dimensions added to each control: account_id, connection_name, region."
  default     = ["account_id", "region"]
}

variable "tag_dimensions" {
  type        = list(string)
  description = "Tag keys added as dimensions to each control (e.g. Environment, Owner)."
  default     = []
}

locals {
  common_dimensions_qualifier_sql = <<-EOQ
  %{~if contains(var.common_dimensions, "connection_name")}, __QUALIFIER___ctx ->> 'connection_name' as connection_name%{endif~}
  %{~if contains(var.common_dimensions, "region")}, __QUALIFIER__region%{endif~}
  %{~if contains(var.common_dimensions, "account_id")}, __QUALIFIER__account_id%{endif~}
  EOQ

  tag_dimensions_qualifier_sql = <<-EOQ
  %{~for dim in var.tag_dimensions},  __QUALIFIER__tags ->> '${dim}' as "${replace(dim, "\"", "\"\"")}"%{endfor~}
  EOQ
}

locals {
  common_dimensions_sql = replace(local.common_dimensions_qualifier_sql, "__QUALIFIER__", "")
  tag_dimensions_sql    = replace(local.tag_dimensions_qualifier_sql, "__QUALIFIER__", "")
}

// ---------------------------------------------------------------- tagging policy

variable "mandatory_tags" {
  type        = list(string)
  description = "Tag keys every resource must have."
  default     = ["Environment", "Service Owner"]
}

variable "prohibited_tags" {
  type        = list(string)
  description = "Tag keys that must not be used."
  default     = []
}

variable "expected_tag_values" {
  type        = map(list(string))
  description = "Allowed values per tag key. SQL LIKE patterns are supported, e.g. {Environment = [\"prod\", \"dev%\"]}."
  default     = {}
}

variable "tag_limit" {
  type        = number
  description = "Maximum number of tags per resource."
  default     = 45
}
