terraform {
  required_version = "= 1.14.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "= 6.38.0" }
  }
}
provider "aws" {
  region              = "us-east-2"
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "oficina", Environment = "staging", Component = "auth-lambda", Owner = var.owner }
  }
}
