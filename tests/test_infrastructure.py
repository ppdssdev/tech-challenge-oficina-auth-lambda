"""Offline structural security policy: no AWS calls or provisioned fixture IDs."""
from pathlib import Path

import hcl2
from hcl2.utils import SerializationOptions

ROOT = Path(__file__).resolve().parents[1] / 'infra/terraform'


def resources():
    result = {}
    for file in ROOT.glob('*.tf'):
        for block in hcl2.loads(file.read_text(), serialization_options=SerializationOptions(strip_string_quotes=True)).get('resource', []):
            for kind, names in block.items():
                for name, value in names.items():
                    result[kind, name] = value
    return result


def test_resource_inventory_excludes_network_database_and_secret_creation():
    allowed = {'aws_lambda_function', 'aws_lambda_permission', 'aws_iam_role', 'aws_iam_role_policy',
               'aws_cloudwatch_log_group', 'aws_apigatewayv2_api', 'aws_apigatewayv2_stage',
               'aws_apigatewayv2_route', 'aws_apigatewayv2_integration', 'aws_lambda_code_signing_config'}
    assert {kind for kind, _ in resources()} == allowed
    all_tf = '\n'.join(file.read_text() for file in ROOT.glob('*.tf'))
    assert 'secret_version' not in all_tf
    assert 'DATABASE_URL' not in all_tf
    assert 'PRIVATE_KEY' not in all_tf
    assert 'terraform_remote_state' not in all_tf


def test_public_endpoint_has_no_activation_switch_and_route_is_restricted():
    data = resources()
    assert data['aws_apigatewayv2_api', 'auth']['disable_execute_api_endpoint'] is True
    route = data['aws_apigatewayv2_route', 'token']
    assert route['route_key'] == 'POST /auth/token'
    assert route['authorization_type'] == 'AWS_IAM'
    assert data['aws_apigatewayv2_integration', 'auth']['payload_format_version'] == '2.0'
    permission = data['aws_lambda_permission', 'gateway']
    assert permission['source_arn'].endswith('/staging/POST/auth/token')
    assert permission['source_account'] == '${var.aws_account_id}'


def test_runtime_limits_network_and_no_plaintext_secret_env():
    function = resources()['aws_lambda_function', 'auth']
    assert function['runtime'] == 'python3.13'
    assert function['architectures'] == ['x86_64']
    assert function['timeout'] == 10 and function['memory_size'] == 256
    assert function['reserved_concurrent_executions'] == '${var.synthetic_test_window ? 2 : 0}'
    assert function['vpc_config'][0]['security_group_ids'] == ['${var.network_contract.auth_security_group_id}']
    assert set(function['environment'][0]['variables']) == {
        'APP_ENV', 'AUTH_SECRET_ARN', 'AUTH_SYNTHETIC_CUSTOMER_IDS', 'DB_HOST', 'DB_PORT', 'DB_NAME',
        'JWT_ISSUER', 'JWT_AUDIENCE', 'JWT_KID'}


def test_logging_is_bounded_and_has_no_identifiers_or_payload():
    for (kind, _), resource in resources().items():
        if kind == 'aws_cloudwatch_log_group':
            assert resource['retention_in_days'] == 7
    stage = resources()['aws_apigatewayv2_stage', 'staging']
    assert stage['default_route_settings'][0]['throttling_rate_limit'] == 1
    logs = stage['access_log_settings'][0]['format']
    for prohibited in ('body', 'sourceIp', 'identity', 'authorization', 'cpf'):
        assert prohibited not in logs


def test_iam_has_scoped_secrets_and_logs_and_denies_eni_access_from_code():
    policy = resources()['aws_iam_role_policy', 'execution']['policy']
    assert 'secretsmanager:GetSecretValue' in policy and 'var.secret_arn' in policy
    assert 'lambda:SourceFunctionArn' in policy and 'Deny' in policy
    for forbidden in ('AdministratorAccess', 'secretsmanager:*', 'kms:*', 'rds:*', 'logs:CreateLogGroup'):
        assert forbidden not in policy


def test_unsigned_or_untrusted_code_cannot_be_deployed_and_runtime_cannot_sign():
    data = resources()
    assert data['aws_lambda_function', 'auth']['code_signing_config_arn'] == '${aws_lambda_code_signing_config.auth.arn}'
    signing = data['aws_lambda_code_signing_config', 'auth']
    assert signing['policies'][0]['untrusted_artifact_on_deployment'] == 'Enforce'
    assert 'var.signing_profile_version_arns' in signing['allowed_publishers'][0]['signing_profile_version_arns']
    policy = data['aws_iam_role_policy', 'execution']['policy']
    for action in ('signer:', 'lambda:UpdateFunctionCode', 'lambda:PutFunctionCodeSigningConfig'):
        assert action not in policy
