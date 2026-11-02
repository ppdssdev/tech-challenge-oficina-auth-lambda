locals {
  name               = "oficina-staging-auth"
  function_arn       = "arn:aws:lambda:us-east-2:${var.aws_account_id}:function:${local.name}"
  execution_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${local.name}-execution"
}
data "aws_subnet" "private" {
  for_each = toset(var.network_contract.private_subnet_ids)
  id       = each.value
}
data "aws_security_group" "auth" { id = var.network_contract.auth_security_group_id }
data "aws_security_group" "rds" { id = var.rds_contract.db_security_group_id }
data "aws_db_instance" "rds" { db_instance_identifier = var.rds_contract.rds_identifier }
data "aws_vpc_endpoint" "secrets" {
  count = var.secrets_endpoint_id == null ? 0 : 1
  id    = var.secrets_endpoint_id
}
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}
resource "aws_iam_role" "lambda" {
  name = "${local.name}-execution"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "execution" {
  role = aws_iam_role.lambda.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda.arn}:*" },
    # AWS requires these ENI management actions with Resource *. Deny their use by function code below.
    { Effect = "Allow", Action = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"], Resource = "*" },
    { Effect = "Deny", Action = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"], Resource = "*", Condition = { ArnEquals = { "lambda:SourceFunctionArn" = local.function_arn } } },
    ], var.secrets_endpoint_id == null ? [] : [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.secret_arn,
    Condition = { StringEquals = { "aws:SourceVpce" = var.secrets_endpoint_id } } }
  ]) })
}
resource "aws_lambda_code_signing_config" "auth" {
  description = "Only reviewed, immutable signing profile versions may deploy auth code."
  allowed_publishers {
    signing_profile_version_arns = sort(tolist(var.signing_profile_version_arns))
  }
  policies {
    untrusted_artifact_on_deployment = "Enforce"
  }
}
resource "aws_lambda_function" "auth" {
  function_name                  = local.name
  role                           = aws_iam_role.lambda.arn
  filename                       = var.artifact_path
  source_code_hash               = filebase64sha256(var.artifact_path)
  code_signing_config_arn        = aws_lambda_code_signing_config.auth.arn
  runtime                        = "python3.13"
  architectures                  = ["x86_64"]
  handler                        = "auth.handler.lambda_handler"
  timeout                        = 10
  memory_size                    = 256
  reserved_concurrent_executions = var.synthetic_test_window ? 2 : 0
  vpc_config {
    subnet_ids         = var.network_contract.private_subnet_ids
    security_group_ids = [var.network_contract.auth_security_group_id]
  }
  environment {
    variables = {
      APP_ENV                     = "staging"
      AUTH_SECRET_ARN             = var.secret_arn
      AUTH_SYNTHETIC_CUSTOMER_IDS = jsonencode(sort(tolist(var.synthetic_customer_ids)))
      DB_HOST                     = var.rds_contract.db_endpoint
      DB_PORT                     = tostring(var.rds_contract.db_port)
      DB_NAME                     = var.rds_contract.db_name
      JWT_ISSUER                  = var.jwt_issuer
      JWT_AUDIENCE                = var.jwt_audience
      JWT_KID                     = var.jwt_kid
    }
  }
  depends_on = [aws_iam_role_policy.execution]
  lifecycle {
    precondition {
      condition     = filesha256(var.artifact_path) == var.artifact_sha256
      error_message = "Artifact checksum mismatch."
    }
    precondition {
      condition = alltrue([for subnet in data.aws_subnet.private :
      subnet.vpc_id == var.network_contract.vpc_id && subnet.owner_id == var.aws_account_id && !subnet.map_public_ip_on_launch]) && length(distinct([for subnet in data.aws_subnet.private : subnet.availability_zone])) == 2
      error_message = "Subnets must belong to the contracted VPC/account and two AZs, without public IP mapping. Review routing separately."
    }
    precondition {
      condition     = data.aws_security_group.auth.vpc_id == var.network_contract.vpc_id && startswith(data.aws_security_group.auth.arn, "arn:aws:ec2:us-east-2:${var.aws_account_id}:security-group/")
      error_message = "Auth SG does not match the contracted VPC/account."
    }
    precondition {
      condition = (data.aws_db_instance.rds.address == var.rds_contract.db_endpoint && data.aws_db_instance.rds.port == var.rds_contract.db_port &&
        data.aws_db_instance.rds.db_name == "oficina_db" && data.aws_db_instance.rds.engine == "postgres" && data.aws_db_instance.rds.storage_encrypted && !data.aws_db_instance.rds.publicly_accessible &&
        contains(data.aws_db_instance.rds.vpc_security_groups, var.rds_contract.db_security_group_id) &&
      data.aws_security_group.rds.vpc_id == var.network_contract.vpc_id && startswith(data.aws_security_group.rds.arn, "arn:aws:ec2:us-east-2:${var.aws_account_id}:security-group/"))
      error_message = "RDS resource differs from supplied outputs or is publicly accessible."
    }
    precondition {
      condition     = !var.synthetic_test_window || (var.secrets_endpoint_id != null && length(var.synthetic_customer_ids) > 0)
      error_message = "Test window requires a separately approved Secrets Manager endpoint and synthetic UUID allowlist."
    }
    precondition {
      condition = var.secrets_endpoint_id == null ? true : (
        data.aws_vpc_endpoint.secrets[0].vpc_id == var.network_contract.vpc_id &&
        data.aws_vpc_endpoint.secrets[0].service_name == "com.amazonaws.us-east-2.secretsmanager" &&
      data.aws_vpc_endpoint.secrets[0].vpc_endpoint_type == "Interface" && data.aws_vpc_endpoint.secrets[0].private_dns_enabled)
      error_message = "Existing Secrets Manager endpoint must use the same VPC and private DNS."
    }
    precondition {
      condition = var.secrets_endpoint_id == null ? true : anytrue([
        for statement in try(jsondecode(data.aws_vpc_endpoint.secrets[0].policy).Statement, []) : try(
          statement.Effect == "Allow" && statement.Principal == "*" &&
          contains(try(tolist(statement.Action), [tostring(statement.Action)]), "secretsmanager:GetSecretValue") &&
          contains(try(tolist(statement.Resource), [tostring(statement.Resource)]), var.secret_arn) &&
          statement.Condition.ArnEquals["aws:PrincipalArn"] == local.execution_role_arn,
          false
        )
      ])
      error_message = "Endpoint policy must contain the infra-k3s auth grant for this exact application secret and execution role ARN."
    }
  }
}
resource "aws_apigatewayv2_api" "auth" {
  name                         = local.name
  protocol_type                = "HTTP"
  disable_execute_api_endpoint = true # No custom domain/mapping exists. No public activation toggle.
}
resource "aws_apigatewayv2_integration" "auth" {
  api_id                 = aws_apigatewayv2_api.auth.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.auth.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 12000
}
resource "aws_apigatewayv2_route" "token" {
  api_id             = aws_apigatewayv2_api.auth.id
  route_key          = "POST /auth/token"
  target             = "integrations/${aws_apigatewayv2_integration.auth.id}"
  authorization_type = "AWS_IAM"
}
resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 7
}
resource "aws_apigatewayv2_stage" "staging" {
  api_id      = aws_apigatewayv2_api.auth.id
  name        = "staging"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit   = 2
    throttling_rate_limit    = 1
    detailed_metrics_enabled = false
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.gateway.arn
    format          = jsonencode({ requestId = "$context.requestId", status = "$context.status", routeKey = "$context.routeKey" })
  }
}
resource "aws_lambda_permission" "gateway" {
  statement_id   = "OnlyAuthTokenRoute"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.auth.function_name
  principal      = "apigateway.amazonaws.com"
  source_account = var.aws_account_id
  source_arn     = "${aws_apigatewayv2_api.auth.execution_arn}/staging/POST/auth/token"
}
output "function_name" { value = aws_lambda_function.auth.function_name }
output "function_arn" { value = aws_lambda_function.auth.arn }
output "execution_role_arn" { value = aws_iam_role.lambda.arn }
output "http_api_id" { value = aws_apigatewayv2_api.auth.id }
