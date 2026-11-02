# Synthetic offline fixtures only. No AWS provider calls: every run uses mock_provider.
mock_provider "aws" {
  # Generate computed attributes during plan for these offline tests.
  override_during = plan

  mock_data "aws_security_group" {
    defaults = {
      vpc_id = "vpc-00000000000000001"
      arn    = "arn:aws:ec2:us-east-2:123456789012:security-group/sg-00000000000000001"
    }
  }
  mock_data "aws_db_instance" {
    defaults = {
      address             = "synthetic.us-east-2.rds.amazonaws.com"
      port                = 5432
      db_name             = "oficina_db"
      engine              = "postgres"
      storage_encrypted   = true
      publicly_accessible = false
      vpc_security_groups = ["sg-00000000000000003"]
    }
  }
}
override_resource {
  # This ARN feeds jsonencode in the execution policy: an unknown ARN makes
  # the entire policy unknown, including its Secrets Manager statement.
  # Override only the dependency; assertions still inspect the real policy.
  target          = aws_cloudwatch_log_group.lambda
  override_during = plan
  values = {
    arn = "arn:aws:logs:us-east-2:123456789012:log-group:/aws/lambda/oficina-staging-auth"
  }
}
override_data {
  target = data.aws_subnet.private["subnet-00000000000000001"]
  values = { vpc_id = "vpc-00000000000000001", owner_id = "123456789012", map_public_ip_on_launch = false, availability_zone = "us-east-2a" }
}
override_data {
  target = data.aws_subnet.private["subnet-00000000000000002"]
  values = { vpc_id = "vpc-00000000000000001", owner_id = "123456789012", map_public_ip_on_launch = false, availability_zone = "us-east-2b" }
}
variables {
  aws_account_id               = "123456789012"
  owner                        = "offline-fixture"
  artifact_path                = "tests/fixture.zip"
  artifact_sha256              = filesha256("tests/fixture.zip")
  secret_arn                   = "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic-AbCd12"
  jwt_issuer                   = "https://synthetic.invalid"
  jwt_audience                 = "synthetic"
  jwt_kid                      = "synthetic"
  signing_profile_version_arns = ["arn:aws:signer:us-east-2:123456789012:/signing-profiles/synthetic/AbCd123456"]
  network_contract = {
    schema_version        = 3, account_id = "123456789012", region = "us-east-2", environment = "staging",
    vpc_id                = "vpc-00000000000000001", private_subnet_ids = ["subnet-00000000000000001", "subnet-00000000000000002"],
    app_security_group_id = "sg-00000000000000001", auth_security_group_id = "sg-00000000000000002"
  }
  rds_contract = {
    rds_identifier = "synthetic", db_endpoint = "synthetic.us-east-2.rds.amazonaws.com", db_port = 5432,
    db_name        = "oficina_db", db_security_group_id = "sg-00000000000000003"
  }
}
run "closed_by_default" {
  command = plan
  assert {
    condition     = aws_lambda_function.auth.reserved_concurrent_executions == 0 && aws_apigatewayv2_api.auth.disable_execute_api_endpoint
    error_message = "Public/unguarded invocation must stay closed."
  }
  assert {
    condition     = aws_apigatewayv2_route.token.authorization_type == "AWS_IAM"
    error_message = "Route must require AWS IAM."
  }
  assert {
    condition = (
      aws_lambda_code_signing_config.auth.policies[0].untrusted_artifact_on_deployment == "Enforce" &&
      toset(aws_lambda_code_signing_config.auth.allowed_publishers[0].signing_profile_version_arns) == var.signing_profile_version_arns
    )
    error_message = "Code signing must enforce the exact authorized profile versions."
  }
}
run "window_requires_private_endpoint" {
  command = plan
  variables {
    synthetic_test_window  = true
    synthetic_customer_ids = ["00000000-0000-4000-8000-000000000001"]
  }
  expect_failures = [aws_lambda_function.auth]
}
run "old_database_contract_rejected" {
  command = plan
  variables {
    rds_contract = {
      rds_identifier = "synthetic", db_endpoint = "synthetic.us-east-2.rds.amazonaws.com", db_port = 5432,
      db_name        = "oficina", db_security_group_id = "sg-00000000000000003"
    }
  }
  expect_failures = [var.rds_contract]
}
run "old_network_contract_rejected" {
  command = plan
  variables {
    network_contract = {
      schema_version        = 2, account_id = "123456789012", region = "us-east-2", environment = "staging",
      vpc_id                = "vpc-00000000000000001", private_subnet_ids = ["subnet-00000000000000001", "subnet-00000000000000002"],
      app_security_group_id = "sg-00000000000000001", auth_security_group_id = "sg-00000000000000002"
    }
  }
  expect_failures = [var.network_contract]
}

run "synthetic_window_with_matching_endpoint_policy" {
  command = plan
  variables {
    secrets_endpoint_id    = "vpce-00000000000000001"
    synthetic_test_window  = true
    synthetic_customer_ids = ["00000000-0000-4000-8000-000000000001"]
  }
  override_data {
    target = data.aws_vpc_endpoint.secrets[0]
    values = {
      vpc_id              = "vpc-00000000000000001"
      service_name        = "com.amazonaws.us-east-2.secretsmanager"
      vpc_endpoint_type   = "Interface"
      private_dns_enabled = true
      policy              = <<-POLICY
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "secretsmanager:GetSecretValue", "Resource": "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic-AbCd12", "Condition": {"ArnEquals": {"aws:PrincipalArn": "arn:aws:iam::123456789012:role/oficina-staging-auth-execution"}}}]}
POLICY
    }
  }
  assert {
    condition = (aws_lambda_function.auth.reserved_concurrent_executions == 2 &&
      aws_apigatewayv2_api.auth.disable_execute_api_endpoint &&
      jsondecode(aws_iam_role_policy.execution.policy).Statement[3].Resource == var.secret_arn &&
    jsondecode(aws_iam_role_policy.execution.policy).Statement[3].Condition.StringEquals["aws:SourceVpce"] == var.secrets_endpoint_id)
    error_message = "Synthetic window must retain HTTP block and scope Secrets Manager to exact ARN/endpoint."
  }
}
run "endpoint_without_auth_grant_rejected" {
  command = plan
  variables {
    secrets_endpoint_id = "vpce-00000000000000001"
  }
  override_data {
    target = data.aws_vpc_endpoint.secrets[0]
    values = {
      vpc_id            = "vpc-00000000000000001", service_name = "com.amazonaws.us-east-2.secretsmanager",
      vpc_endpoint_type = "Interface", private_dns_enabled = true,
      policy            = <<-POLICY
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "secretsmanager:GetSecretValue", "Resource": "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic-AbCd12", "Condition": {"ArnEquals": {"aws:PrincipalArn": "arn:aws:iam::123456789012:role/another-role"}}}]}
POLICY
    }
  }
  expect_failures = [aws_lambda_function.auth]
}
run "endpoint_for_another_secret_rejected" {
  command = plan
  variables {
    secrets_endpoint_id = "vpce-00000000000000001"
  }
  override_data {
    target = data.aws_vpc_endpoint.secrets[0]
    values = {
      vpc_id            = "vpc-00000000000000001", service_name = "com.amazonaws.us-east-2.secretsmanager",
      vpc_endpoint_type = "Interface", private_dns_enabled = true,
      policy            = <<-POLICY
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": "*", "Action": "secretsmanager:GetSecretValue", "Resource": "arn:aws:secretsmanager:us-east-2:123456789012:secret:another-secret-AbCd12", "Condition": {"ArnEquals": {"aws:PrincipalArn": "arn:aws:iam::123456789012:role/oficina-staging-auth-execution"}}}]}
POLICY
    }
  }
  expect_failures = [aws_lambda_function.auth]
}
run "wildcard_secret_rejected" {
  command = plan
  variables {
    secret_arn = "arn:aws:secretsmanager:us-east-2:123456789012:secret:synthetic-*-AbCd12"
  }
  expect_failures = [var.secret_arn]
}

run "empty_signing_trust_rejected" {
  command = plan
  variables {
    signing_profile_version_arns = []
  }
  expect_failures = [var.signing_profile_version_arns]
}

run "unversioned_signing_profile_rejected" {
  command = plan
  variables {
    signing_profile_version_arns = ["arn:aws:signer:us-east-2:123456789012:/signing-profiles/synthetic"]
  }
  expect_failures = [var.signing_profile_version_arns]
}

run "foreign_signing_account_rejected" {
  command = plan
  variables {
    signing_profile_version_arns = ["arn:aws:signer:us-east-2:999999999999:/signing-profiles/synthetic/AbCd123456"]
  }
  expect_failures = [var.signing_profile_version_arns]
}
