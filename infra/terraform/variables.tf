variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Provide the independently verified AWS account ID."
  }
}
variable "owner" {
  type = string
  validation {
    condition     = length(trimspace(var.owner)) > 0
    error_message = "Owner is required."
  }
}
variable "network_contract" {
  type = object({
    schema_version = number, account_id = string, region = string, environment = string,
    vpc_id         = string, private_subnet_ids = list(string), app_security_group_id = string, auth_security_group_id = string
  })
  validation {
    condition = (var.network_contract.schema_version == 3 && var.network_contract.account_id == var.aws_account_id &&
      var.network_contract.region == "us-east-2" && var.network_contract.environment == "staging" &&
      can(regex("^vpc-[0-9a-f]{8,17}$", var.network_contract.vpc_id)) &&
      length(var.network_contract.private_subnet_ids) == 2 && length(distinct(var.network_contract.private_subnet_ids)) == 2 &&
      alltrue([for id in var.network_contract.private_subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", id))]) &&
      can(regex("^sg-[0-9a-f]{8,17}$", var.network_contract.auth_security_group_id)) &&
      can(regex("^sg-[0-9a-f]{8,17}$", var.network_contract.app_security_group_id)) &&
    var.network_contract.app_security_group_id != var.network_contract.auth_security_group_id)
    error_message = "Require the complete matching K3s integration_contract v3 and two distinct private subnets."
  }
}
variable "rds_contract" {
  description = "Assemble from the actual infra-rds outputs, without admin_secret_arn."
  type        = object({ rds_identifier = string, db_endpoint = string, db_port = number, db_name = string, db_security_group_id = string })
  validation {
    condition = (var.rds_contract.db_port == 5432 && var.rds_contract.db_name == "oficina_db" &&
      can(regex("^[a-zA-Z0-9.-]+\\.us-east-2\\.rds\\.amazonaws\\.com$", var.rds_contract.db_endpoint)) &&
    can(regex("^sg-[0-9a-f]{8,17}$", var.rds_contract.db_security_group_id)))
    error_message = "Require real Ohio RDS outputs, port 5432 and oficina_db."
  }
}
variable "artifact_path" {
  description = "Validated AWS Signer signed deployment ZIP; the deterministic unsigned build is not deployable."
  type        = string
  validation {
    condition     = endswith(var.artifact_path, ".zip") && fileexists(var.artifact_path)
    error_message = "Build and validate the existing Python 3.13 x86_64 ZIP first."
  }
}
variable "artifact_sha256" {
  description = "SHA-256 of the final signed ZIP, after layout/secret inspection and runtime smoke."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.artifact_sha256))
    error_message = "Provide the independently verified artifact SHA-256."
  }
}
variable "signing_profile_version_arns" {
  description = "Reviewed AWS Signer profile version ARNs (AWSLambda-SHA384-ECDSA), existing in the authorized account/region. No signing API calls in this root."
  type        = set(string)
  nullable    = false
  validation {
    condition = length(var.signing_profile_version_arns) >= 1 && length(var.signing_profile_version_arns) <= 20 && alltrue([
      for arn in var.signing_profile_version_arns : can(regex(
        "^arn:aws:signer:us-east-2:${var.aws_account_id}:/signing-profiles/[A-Za-z0-9_]{2,64}/[A-Za-z0-9]{10}$", arn
      ))
    ])
    error_message = "Require 1–20 immutable signing profile version ARNs in the authorized Ohio account; aliases, profiles without version and wildcards are forbidden."
  }
}
variable "secret_arn" {
  description = "Existing application secret reference only. Never pass a secret value to Terraform."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:us-east-2:${var.aws_account_id}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$", var.secret_arn))
    error_message = "Provide the full application secret ARN in the authorized account/region."
  }
}
variable "secrets_endpoint_id" {
  description = "Existing separately approved interface endpoint. This root never creates it."
  type        = string
  default     = null
  validation {
    condition     = var.secrets_endpoint_id == null ? true : can(regex("^vpce-[0-9a-f]{8,17}$", var.secrets_endpoint_id))
    error_message = "Expected an existing VPC endpoint ID."
  }
}
variable "jwt_issuer" {
  type = string
  validation {
    condition     = startswith(var.jwt_issuer, "https://") && length(var.jwt_issuer) > 8
    error_message = "Issuer must match Java and use https."
  }
}
variable "jwt_audience" {
  type = string
  validation {
    condition     = length(trimspace(var.jwt_audience)) > 0
    error_message = "Audience required."
  }
}
variable "jwt_kid" {
  type = string
  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,128}$", var.jwt_kid))
    error_message = "Invalid key ID."
  }
}
variable "synthetic_test_window" {
  description = "Only controlled direct invokes for explicitly allowed synthetic identities; never opens HTTP."
  type        = bool
  default     = false
}
variable "synthetic_customer_ids" {
  type    = set(string)
  default = []
  validation {
    condition     = alltrue([for id in var.synthetic_customer_ids : can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", id))])
    error_message = "Only canonical UUIDs of synthetic fixtures."
  }
}
