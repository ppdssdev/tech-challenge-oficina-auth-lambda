terraform {
  backend "s3" {
    bucket       = "oficina-staging-tfstate-797781632267"
    key          = "staging/lambda/terraform.tfstate"
    region       = "us-east-2"
    use_lockfile = true
    encrypt      = true
  }
}
