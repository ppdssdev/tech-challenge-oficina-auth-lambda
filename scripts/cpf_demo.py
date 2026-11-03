"""Operator-only staging smoke. Importing this module never creates an AWS client."""
import argparse
import getpass
import http.client
import json
import logging
import os
from pathlib import Path
import re
import resource
import signal
import ssl
import stat
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID

ACCOUNT = '797781632267'
REGION = 'us-east-2'
PROFILE = 'oficina-staging'
FUNCTION = 'oficina-staging-auth'
ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
API = 't6p562hkc9'
SOURCE = f'arn:aws:execute-api:{REGION}:{ACCOUNT}:{API}/staging/POST/auth/token'
LIMIT = 65536


class CheckFailed(Exception):
    """Only fixed check names may reach the console."""


def require(condition, name):
    if not condition:
        raise CheckFailed(name)


class Evidence:
    def __init__(self):
        self.failed = False

    def check(self, name, condition, actual=None):
        self.failed |= not condition
        row = {'check': name, 'result': 'PASS' if condition else 'FAIL'}
        if type(actual) is int:
            row['http_status'] = actual
        print(json.dumps(row), flush=True)


def private_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= LIMIT,
                'private_input_permissions')
        return json.load(source)


def https_url(value):
    u = urlsplit(value)
    require(u.scheme == 'https' and u.hostname and not u.username and not u.password
            and not u.query and not u.fragment, 'trusted_https_url')
    return u


def validate_inputs(data):
    from auth.request import valid_cpf
    require(data['synthetic_only'] is True, 'synthetic_attestation')
    for name in ('owner', 'other', 'inactive'):
        item = data[name]
        require(valid_cpf(item['cpf']) and str(UUID(item['customer_id'])) == item['customer_id'],
                'synthetic_identity_shape')
    require(valid_cpf(data['missing_cpf']), 'missing_cpf_shape')
    require(len({data[n]['cpf'] for n in ('owner', 'other', 'inactive')}
                | {data['missing_cpf']}) == 4, 'distinct_cpfs')
    require(len({data[n]['customer_id'] for n in ('owner', 'other', 'inactive')}) == 3,
            'distinct_customers')
    for name in ('owner', 'other'):
        require(str(UUID(data[name]['order_id'])) == data[name]['order_id'], 'order_uuid')
    require(data['owner']['order_id'] != data['other']['order_id'], 'distinct_orders')
    require(re.fullmatch(r'[A-Za-z0-9+/]{43}=', data['code_sha256']), 'code_hash')
    require(data['version'] == '$LATEST' or re.fullmatch(r'[1-9][0-9]*', data['version']),
            'explicit_version')
    for name in ('java_url', 'jwks_url', 'issuer'):
        https_url(data[name])
    require(bool(data['audience']) and bool(data['kid']), 'jwt_trust')
    require(re.fullmatch(r'[A-Za-z0-9.-]+\.us-east-2\.rds\.amazonaws\.com', data['db_host']),
            'rds_hostname')
    require(Path(data['db_ca_file']).is_file(), 'rds_ca')


def clients():
    # Refuse environment credentials/endpoints and profiles with implicit endpoint overrides.
    require(not any(k.startswith('AWS_ENDPOINT_URL') or k in {
        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
        'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_ROLE_ARN', 'AWS_CONTAINER_CREDENTIALS_FULL_URI',
        'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI',
    } for k in os.environ), 'aws_environment_override')
    import boto3
    from botocore.config import Config
    os.environ['AWS_EC2_METADATA_DISABLED'] = 'true'
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    cfg = Config(connect_timeout=2, read_timeout=15, retries={'total_max_attempts': 1},
                 ignore_configured_endpoint_urls=True)
    result = {name: session.client(name, region_name=REGION, config=cfg)
              for name in ('sts', 'lambda', 'apigatewayv2')}
    identity = result['sts'].get_caller_identity()
    require(identity['Account'] == ACCOUNT and identity['Arn'].startswith(
        (f'arn:aws:iam::{ACCOUNT}:', f'arn:aws:sts::{ACCOUNT}:')), 'aws_account')
    return result


def preflight(c, data, evidence):
    lam, gateway = c['lambda'], c['apigatewayv2']
    require(lam.get_function_concurrency(FunctionName=FUNCTION).get(
        'ReservedConcurrentExecutions') == 0, 'initial_concurrency_zero')
    configuration = lam.get_function_configuration(FunctionName=FUNCTION, Qualifier=data['version'])
    require(configuration['FunctionArn'] in (ARN, ARN + ':' + data['version'])
            and configuration['Version'] == data['version']
            and configuration['Runtime'] == 'python3.13'
            and configuration['State'] == 'Active'
            and configuration['LastUpdateStatus'] == 'Successful'
            and configuration['CodeSha256'] == data['code_sha256'], 'lambda_release')
    require(configuration['VpcConfig']['SubnetIds'] and configuration['VpcConfig']['SecurityGroupIds'],
            'lambda_vpc')
    env = configuration['Environment']['Variables']
    require(env['APP_ENV'] == 'staging' and env['DB_HOST'] == data['db_host']
            and env['DB_NAME'] == 'oficina_db' and env['DB_PORT'] == '5432', 'lambda_rds_contract')
    require(env['AUTH_SECRET_ARN'].startswith(f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:'),
            'secret_reference')
    require(all(env['JWT_' + key.upper()] == data[key] for key in ('issuer', 'audience', 'kid')),
            'lambda_jwt_trust')
    allowed = json.loads(env['AUTH_SYNTHETIC_CUSTOMER_IDS'])
    require(all(data[n]['customer_id'] in allowed for n in ('owner', 'other', 'inactive')),
            'synthetic_allowlist')
    signing_arn = lam.get_function_code_signing_config(FunctionName=FUNCTION)['CodeSigningConfigArn']
    signing = lam.get_code_signing_config(CodeSigningConfigArn=signing_arn)['CodeSigningConfig']
    require(signing['CodeSigningPolicies']['UntrustedArtifactOnDeployment'] == 'Enforce'
            and configuration.get('SigningProfileVersionArn') in
            signing['AllowedPublishers']['SigningProfileVersionArns']
            and configuration.get('SigningJobArn'), 'signed_release_enforced')
    evidence.check('lambda_profile_release_signing_vpc_allowlist', True)
    api = gateway.get_api(ApiId=API)
    require(api['ProtocolType'] == 'HTTP' and api['DisableExecuteApiEndpoint'] is True,
            'gateway_endpoint_disabled')
    routes = gateway.get_routes(ApiId=API)
    require(not routes.get('NextToken') and len(routes['Items']) == 1, 'gateway_route_inventory')
    route = routes['Items'][0]
    require(route['RouteKey'] == 'POST /auth/token' and route['AuthorizationType'] == 'AWS_IAM'
            and route['Target'].startswith('integrations/'), 'gateway_iam_route')
    integration = gateway.get_integration(ApiId=API, IntegrationId=route['Target'].split('/')[1])
    target = ARN if data['version'] == '$LATEST' else ARN + ':' + data['version']
    require(integration['IntegrationType'] == 'AWS_PROXY'
            and integration['PayloadFormatVersion'] == '2.0'
            and integration['IntegrationUri'] in (target,
                f'arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{target}/invocations'),
            'gateway_integration')
    stage = gateway.get_stage(ApiId=API, StageName='staging')
    require(stage['AutoDeploy'] is True and stage.get('DeploymentId')
            and not stage.get('LastDeploymentStatusMessage'), 'gateway_stage')
    deployment = gateway.get_deployment(ApiId=API, DeploymentId=stage['DeploymentId'])
    require(deployment['DeploymentStatus'] == 'DEPLOYED', 'gateway_deployment')
    statements = json.loads(lam.get_policy(FunctionName=FUNCTION)['Policy'])['Statement']
    require(len(statements) == 1, 'lambda_policy_inventory')
    p = statements[0]
    require(p['Effect'] == 'Allow' and p['Principal'] == {'Service': 'apigateway.amazonaws.com'}
            and p['Action'] == 'lambda:InvokeFunction' and p['Resource'] == ARN
            and p['Condition']['ArnLike']['AWS:SourceArn'] == SOURCE
            and p['Condition']['StringEquals']['AWS:SourceAccount'] == ACCOUNT,
            'gateway_lambda_permission')
    evidence.check('gateway_config_only_no_http_invocation', True)


# Catalog-only privilege audit. No attempted DML/DDL, role changes or fixture creation.
PRIVILEGES_SQL = """
SELECT current_user = 'oficina_lambda'
 AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE pg_has_role(current_user, oid, 'MEMBER')
   AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
        OR rolname NOT IN ('oficina_lambda', 'oficina_auth_reader')))
 AND has_schema_privilege(current_user, 'auth', 'USAGE')
 AND has_table_privilege(current_user, 'auth.customer_identity_v2', 'SELECT')
 AND NOT has_database_privilege(current_user, current_database(), 'CREATE,TEMP')
 AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname !~ '^pg_'
   AND nspname <> 'information_schema' AND has_schema_privilege(current_user, oid, 'CREATE'))
 AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
   AND c.relkind IN ('r','p','v','m','f') AND (
     has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
     OR has_any_column_privilege(current_user, c.oid, 'INSERT,UPDATE,REFERENCES')
     OR (c.oid <> 'auth.customer_identity_v2'::regclass AND (
       has_table_privilege(current_user, c.oid, 'SELECT')
       OR has_any_column_privilege(current_user, c.oid, 'SELECT')))))
 AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind='S'
   AND has_sequence_privilege(current_user, c.oid, 'USAGE,UPDATE'))
 AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
   AND has_function_privilege(current_user, p.oid, 'EXECUTE'))
"""


def audit_database(data, password, evidence, connect=None):
    if connect is None:
        import psycopg
        connect = psycopg.connect
    with connect(host=data['db_host'], port=5432, dbname='oficina_db', user='oficina_lambda',
                 password=password, sslmode='verify-full', sslrootcert=data['db_ca_file'],
                 connect_timeout=3,
                 options='-c statement_timeout=3000 -c default_transaction_read_only=on') as conn:
        require(conn.execute('SHOW transaction_read_only').fetchone() == ('on',), 'db_read_only')
        require(conn.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
                == (True,), 'db_tls')
        require(conn.execute(PRIVILEGES_SQL).fetchone() == (True,), 'db_effective_privileges')
        for name, active in (('owner', True), ('other', True), ('inactive', False), ('missing', None)):
            cpf = data['missing_cpf'] if name == 'missing' else data[name]['cpf']
            rows = conn.execute('SELECT customer_id, active FROM auth.customer_identity_v2 '
                                'WHERE cpf = %s LIMIT 2', (cpf,)).fetchall()
            require(not rows if name == 'missing' else len(rows) == 1
                    and str(rows[0][0]) == data[name]['customer_id'] and rows[0][1] is active,
                    'db_identity_' + name)
        conn.rollback()
    evidence.check('rds_view_v2_real_select_privileges_status', True)


def http_get(url, token=None):
    u = https_url(url)
    # No proxy env, cookies, redirects, insecure TLS option or wire logging.
    conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=5,
                                       context=ssl.create_default_context())
    try:
        headers = {'Accept': 'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        conn.request('GET', u.path or '/', headers=headers)
        response = conn.getresponse()
        body = response.read(LIMIT + 1)
        require(len(body) <= LIMIT, 'http_response_size')
        return response.status, body
    finally:
        conn.close()


def verify_token(token, data, customer_id, jwks):
    import jwt
    header = jwt.get_unverified_header(token)
    require(header.get('alg') == 'RS256' and header.get('kid') == data['kid'], 'jwt_header')
    keys = [k for k in jwks['keys'] if k.get('kid') == data['kid']]
    require(len(keys) == 1 and keys[0].get('kty') == 'RSA'
            and keys[0].get('use', 'sig') == 'sig'
            and keys[0].get('alg', 'RS256') == 'RS256'
            and not set(keys[0]) & {'d', 'p', 'q', 'dp', 'dq', 'qi'}, 'trusted_jwks')
    key = jwt.PyJWK.from_dict(keys[0], algorithm='RS256').key
    require(key.key_size >= 2048, 'rsa_key_size')
    claims = jwt.decode(token, key, algorithms=['RS256'], issuer=data['issuer'],
                        audience=data['audience'], options={
                            'require': ['iss', 'aud', 'sub', 'role', 'iat', 'nbf', 'exp']})
    require(set(claims) == {'iss', 'aud', 'sub', 'role', 'iat', 'nbf', 'exp'}
            and claims['sub'] == customer_id and claims['role'] == 'CUSTOMER'
            and all(type(claims[n]) is int for n in ('iat', 'nbf', 'exp'))
            and claims['nbf'] == claims['iat'] and claims['exp'] - claims['iat'] == 900,
            'jwt_claims')


def invoke(lam, cpf, version):
    event = {'version': '2.0', 'rawPath': '/auth/token', 'rawQueryString': '',
             'requestContext': {'http': {'method': 'POST', 'path': '/auth/token'}},
             'headers': {'content-type': 'application/json'}, 'isBase64Encoded': False,
             'body': json.dumps({'cpf': cpf})}
    result = lam.invoke(FunctionName=FUNCTION, Qualifier=version, InvocationType='RequestResponse',
                        LogType='None', Payload=json.dumps(event).encode())
    stream = result['Payload']
    try:
        raw = stream.read(LIMIT + 1)
    finally:
        stream.close()
    require(result['StatusCode'] == 200 and not result.get('FunctionError')
            and result.get('ExecutedVersion') == version and len(raw) <= LIMIT, 'lambda_invocation')
    response = json.loads(raw)
    require(response['headers'].get('Cache-Control') == 'no-store', 'no_store')
    return response['statusCode'], json.loads(response['body'])


def scenarios(lam, data, evidence, get=http_get):
    status, body = get(data['jwks_url'])
    require(status == 200, 'jwks_http')
    jwks = json.loads(body)
    tokens = {}
    for name in ('owner', 'other'):
        status, body = invoke(lam, data[name]['cpf'], data['version'])
        require(status == 200 and set(body) == {'access_token', 'token_type', 'expires_in'}
                and body['token_type'] == 'Bearer' and body['expires_in'] == 900, 'token_' + name)
        tokens[name] = body['access_token']
        verify_token(tokens[name], data, data[name]['customer_id'], jwks)
        evidence.check('lambda_direct_rs256_' + name, True)
    for name, cpf, expected, code in (
        ('missing', data['missing_cpf'], 401, 'AUTHENTICATION_FAILED'),
        ('inactive', data['inactive']['cpf'], 401, 'AUTHENTICATION_FAILED'),
        ('invalid', 'invalid', 400, 'INVALID_REQUEST'),
    ):
        status, body = invoke(lam, cpf, data['version'])
        evidence.check('no_token_' + name, status == expected and body == {'code': code}, status)
    pieces = tokens['owner'].split('.')
    pieces[2] = ('A' if pieces[2][0] != 'A' else 'B') + pieces[2][1:]
    tampered = '.'.join(pieces)
    base = data['java_url'].rstrip('/')
    owner = '/api/v1/customer/work-orders/' + data['owner']['order_id']
    other = '/api/v1/customer/work-orders/' + data['other']['order_id']
    for name, path, token, expected in (
        ('java_owner', owner, tokens['owner'], 200),
        ('java_other_owner', other, tokens['other'], 200),
        ('java_missing_token', owner, None, 401),
        ('java_invalid_token', owner, 'invalid', 401),
        ('java_tampered_signature', owner, tampered, 401),
        ('java_foreign_order_hidden', owner, tokens['other'], 404),
        ('java_customer_admin_contract', '/api/v1/admin/work-orders', tokens['owner'], 403),
    ):
        status, body = get(base + path, token)
        matches = status == expected
        if expected == 200:
            matches = matches and json.loads(body).get('id') == path.rsplit('/', 1)[1]
        evidence.check(name, matches, status)


def close_window(lam, evidence, sleep=time.sleep):
    signal.alarm(0)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, signal.SIG_IGN)
    for attempt in range(3):
        try:
            lam.put_function_concurrency(FunctionName=FUNCTION, ReservedConcurrentExecutions=0)
            if lam.get_function_concurrency(FunctionName=FUNCTION).get('ReservedConcurrentExecutions') == 0:
                evidence.check('final_concurrency_zero', True)
                return
        except Exception:
            pass  # Never print SDK exceptions: they may include request data.
        sleep(attempt + 1)
    evidence.check('final_concurrency_zero', False)
    raise CheckFailed('cleanup_unconfirmed_use_independent_recovery')


def run_window(lam, work, evidence):
    # finally covers an ambiguous opening timeout: AWS may have accepted the write.
    try:
        lam.put_function_concurrency(FunctionName=FUNCTION, ReservedConcurrentExecutions=2)
        require(lam.get_function_concurrency(FunctionName=FUNCTION).get(
            'ReservedConcurrentExecutions') == 2, 'open_concurrency')
        work()
    finally:
        close_window(lam, evidence)


def interrupted(signum, frame):
    raise CheckFailed('interrupted_or_deadline')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preflight', 'window', 'close', 'watchdog'])
    parser.add_argument('--input', help='Operator-owned mode-0600 JSON outside checkout')
    parser.add_argument('--authorized', action='store_true')
    parser.add_argument('--recovery-armed', action='store_true')
    args = parser.parse_args(argv)
    evidence = Evidence()
    try:
        require(args.authorized and sys.stdin.isatty() and not os.environ.get('CI'), 'operator_gate')
        require(args.mode != 'window' or args.recovery_armed, 'independent_recovery_gate')
        require(getpass.getpass('Digite AUTORIZO SOMENTE STAGING SINTETICO: ')
                == 'AUTORIZO SOMENTE STAGING SINTETICO', 'operator_confirmation')
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        logging.disable(logging.CRITICAL)
        os.umask(0o077)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
            signal.signal(sig, interrupted)
        c = clients()
        evidence.check('aws_profile_account_region', True)
        if args.mode in ('close', 'watchdog'):
            # Second host/session, separately authorized; never opens a window.
            if args.mode == 'watchdog':
                evidence.check('watchdog_armed_240_seconds', True)
                try:
                    time.sleep(240)
                finally:
                    close_window(c['lambda'], evidence)
            else:
                close_window(c['lambda'], evidence)
            return int(evidence.failed)
        data = private_json(args.input)
        validate_inputs(data)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
            signal.signal(sig, interrupted)
        signal.alarm(180)
        preflight(c, data, evidence)
        password = getpass.getpass('Senha do reader (não gravada): ')
        audit_database(data, password, evidence)
        del password
        if args.mode == 'window':
            # Bound the actual opening too; operator must arm watchdog immediately beforehand.
            run_window(c['lambda'], lambda: scenarios(c['lambda'], data, evidence), evidence)
        else:
            require(c['lambda'].get_function_concurrency(FunctionName=FUNCTION).get(
                'ReservedConcurrentExecutions') == 0, 'preflight_final_zero')
        signal.alarm(0)
        return int(evidence.failed)
    except BaseException:
        # No traceback, locals, values, SDK messages, CPF, JWT or HTTP body on any failure.
        print('{"check":"execution","result":"FAIL","detail":"see_runbook_recovery"}', flush=True)
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
