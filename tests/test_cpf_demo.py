"""Network forbidden: real handler/RS256, fake AWS, HTTP and reader connection."""
import copy
import io
import json
import signal
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from auth.handler import handle
from auth.ports import Identity
from auth.service import AuthenticationService
from scripts import cpf_demo as demo


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network forbidden in smoke unit tests')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    previous = {sig: signal.getsignal(sig) for sig in
                (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM)}
    yield
    signal.alarm(0)
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def cpf(seed):
    digits = [int(n) for n in f'{seed:09d}']
    for size in (9, 10):
        value = sum(n * (size + 1 - i) for i, n in enumerate(digits)) * 10 % 11
        digits.append(0 if value == 10 else value)
    return ''.join(map(str, digits))


@pytest.fixture
def data(tmp_path):
    ca = tmp_path / 'ca.crt'
    ca.write_text('mock CA')
    return dict(synthetic_only=True, code_sha256='A' * 43 + '=', version='$LATEST',
                java_url='https://java.test', jwks_url='https://jwks.test/keys',
                issuer='https://issuer.test', audience='oficina', kid='test-key',
                db_host='synthetic.us-east-2.rds.amazonaws.com', db_ca_file=str(ca),
                owner=dict(cpf=cpf(123450001), customer_id=str(UUID(int=1)), order_id=str(UUID(int=11))),
                other=dict(cpf=cpf(123450002), customer_id=str(UUID(int=2)), order_id=str(UUID(int=12))),
                inactive=dict(cpf=cpf(123450003), customer_id=str(UUID(int=3))),
                missing_cpf=cpf(123450004))


@pytest.fixture
def clients(data):
    lam, gateway = Mock(), Mock()
    lam.get_function_concurrency.return_value = {'ReservedConcurrentExecutions': 0}
    lam.get_function_configuration.return_value = dict(
        FunctionArn=demo.ARN, Version='$LATEST', Runtime='python3.13', State='Active',
        LastUpdateStatus='Successful', CodeSha256=data['code_sha256'],
        VpcConfig=dict(SubnetIds=['subnet-test'], SecurityGroupIds=['sg-test']),
        SigningProfileVersionArn='signing-version', SigningJobArn='signing-job',
        Environment={'Variables': dict(APP_ENV='staging', DB_HOST=data['db_host'],
            DB_NAME='oficina_db', DB_PORT='5432',
            AUTH_SECRET_ARN=f'arn:aws:secretsmanager:{demo.REGION}:{demo.ACCOUNT}:secret:test',
            AUTH_SYNTHETIC_CUSTOMER_IDS=json.dumps([data[n]['customer_id'] for n in
                                                   ('owner', 'other', 'inactive')]),
            JWT_ISSUER=data['issuer'], JWT_AUDIENCE=data['audience'], JWT_KID=data['kid'])})
    lam.get_function_code_signing_config.return_value = {'CodeSigningConfigArn': 'signing'}
    lam.get_code_signing_config.return_value = {'CodeSigningConfig': {
        'CodeSigningPolicies': {'UntrustedArtifactOnDeployment': 'Enforce'},
        'AllowedPublishers': {'SigningProfileVersionArns': ['signing-version']}}}
    lam.get_policy.return_value = {'Policy': json.dumps({'Statement': [dict(
        Effect='Allow', Principal={'Service': 'apigateway.amazonaws.com'},
        Action='lambda:InvokeFunction', Resource=demo.ARN, Condition={
            'ArnLike': {'AWS:SourceArn': demo.SOURCE},
            'StringEquals': {'AWS:SourceAccount': demo.ACCOUNT}})]})}
    gateway.get_api.return_value = dict(ProtocolType='HTTP', DisableExecuteApiEndpoint=True)
    gateway.get_routes.return_value = {'Items': [dict(RouteKey='POST /auth/token',
        AuthorizationType='AWS_IAM', Target='integrations/test')]}
    gateway.get_integration.return_value = dict(IntegrationType='AWS_PROXY',
        PayloadFormatVersion='2.0', IntegrationUri=demo.ARN)
    gateway.get_stage.return_value = dict(AutoDeploy=True, DeploymentId='test-deployment')
    gateway.get_deployment.return_value = dict(DeploymentStatus='DEPLOYED')
    return {'lambda': lam, 'apigatewayv2': gateway}


def test_preflight_is_read_only(data, clients, capsys):
    demo.validate_inputs(data)
    demo.preflight(clients, data, demo.Evidence())
    assert all(call[0].startswith('get_') for client in clients.values() for call in client.mock_calls)
    output = capsys.readouterr().out
    assert 'no_http_invocation' in output
    for value in (data['owner']['cpf'], data['owner']['customer_id'], 'secret:test'):
        assert value not in output


@pytest.mark.parametrize('method,field,value', [
    ('get_function_concurrency', 'ReservedConcurrentExecutions', 2),
    ('get_function_configuration', 'Runtime', 'python3.12'),
    ('get_function_configuration', 'State', 'Pending'),
    ('get_function_configuration', 'LastUpdateStatus', 'Failed'),
    ('get_function_configuration', 'CodeSha256', 'wrong'),
    ('get_function_configuration', 'Version', '2'),
    ('get_function_configuration', 'SigningJobArn', None),
    ('get_api', 'DisableExecuteApiEndpoint', False),
    ('get_api', 'ProtocolType', 'WEBSOCKET'),
    ('get_routes', 'NextToken', 'more'),
    ('get_integration', 'PayloadFormatVersion', '1.0'),
    ('get_integration', 'IntegrationUri', 'another-function'),
    ('get_stage', 'DeploymentId', None),
    ('get_deployment', 'DeploymentStatus', 'FAILED'),
])
def test_preflight_drift_blocks_open(data, clients, method, field, value):
    target = clients['lambda'] if method.startswith('get_function') else clients['apigatewayv2']
    getattr(target, method).return_value[field] = value
    with pytest.raises(demo.CheckFailed):
        demo.preflight(clients, data, demo.Evidence())
    clients['lambda'].put_function_concurrency.assert_not_called()
    clients['lambda'].invoke.assert_not_called()


def test_wrong_iam_route_and_wildcard_permission_rejected(data, clients):
    clients['apigatewayv2'].get_routes.return_value['Items'][0]['AuthorizationType'] = 'NONE'
    with pytest.raises(demo.CheckFailed):
        demo.preflight(clients, data, demo.Evidence())
    clients['apigatewayv2'].get_routes.return_value['Items'][0]['AuthorizationType'] = 'AWS_IAM'
    policy = json.loads(clients['lambda'].get_policy.return_value['Policy'])
    policy['Statement'][0]['Condition']['ArnLike']['AWS:SourceArn'] = '*'
    clients['lambda'].get_policy.return_value['Policy'] = json.dumps(policy)
    with pytest.raises(demo.CheckFailed):
        demo.preflight(clients, data, demo.Evidence())


@pytest.mark.parametrize('failure', [None, RuntimeError('sensitive'), KeyboardInterrupt(),
                                    demo.CheckFailed('interrupted_or_deadline')])
def test_window_always_closes_even_after_failure(failure, capsys):
    lam = Mock()
    lam.get_function_concurrency.side_effect = [dict(ReservedConcurrentExecutions=2),
                                                dict(ReservedConcurrentExecutions=0)]
    def work():
        if failure:
            raise failure
    if failure:
        with pytest.raises(type(failure)):
            demo.run_window(lam, work, demo.Evidence())
    else:
        demo.run_window(lam, work, demo.Evidence())
    assert [c.kwargs['ReservedConcurrentExecutions'] for c in lam.put_function_concurrency.call_args_list] == [2, 0]
    assert 'sensitive' not in capsys.readouterr().out


def test_ambiguous_open_timeout_still_closes():
    lam = Mock()
    lam.put_function_concurrency.side_effect = [TimeoutError(), {}]
    lam.get_function_concurrency.return_value = dict(ReservedConcurrentExecutions=0)
    work = Mock()
    with pytest.raises(TimeoutError):
        demo.run_window(lam, work, demo.Evidence())
    work.assert_not_called()
    assert lam.put_function_concurrency.call_args.kwargs['ReservedConcurrentExecutions'] == 0


def test_cleanup_retries_and_never_reports_success_without_confirmation(capsys):
    lam, evidence = Mock(), demo.Evidence()
    lam.get_function_concurrency.return_value = dict(ReservedConcurrentExecutions=2)
    with pytest.raises(demo.CheckFailed):
        demo.close_window(lam, evidence, sleep=lambda _: None)
    assert lam.put_function_concurrency.call_count == 3
    assert evidence.failed
    assert 'PASS' not in capsys.readouterr().out


def test_cleanup_transient_failure_recovers():
    lam = Mock()
    lam.put_function_concurrency.side_effect = [TimeoutError(), {}]
    lam.get_function_concurrency.return_value = dict(ReservedConcurrentExecutions=0)
    demo.close_window(lam, demo.Evidence(), sleep=lambda _: None)
    assert lam.put_function_concurrency.call_count == 2


def test_private_file_mode_and_symlink(tmp_path):
    p = tmp_path / 'fixture.json'
    p.write_text('{}')
    p.chmod(0o644)
    with pytest.raises(demo.CheckFailed):
        demo.private_json(p)
    p.chmod(0o600)
    assert demo.private_json(p) == {}
    link = tmp_path / 'link'
    link.symlink_to(p)
    with pytest.raises(OSError):
        demo.private_json(link)


@pytest.mark.parametrize('url', ['http://java.test', 'https://user:pass@java.test',
                                'https://java.test?cpf=x', 'https://java.test#token'])
def test_unsafe_url_rejected(url):
    with pytest.raises(demo.CheckFailed):
        demo.https_url(url)


def test_unauthorized_cli_never_constructs_clients(monkeypatch, capsys):
    client_factory = Mock()
    monkeypatch.setattr(demo, 'clients', client_factory)
    assert demo.main(['window']) == 1
    client_factory.assert_not_called()
    assert 'FAIL' in capsys.readouterr().out


@pytest.mark.parametrize('sig', [signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM])
def test_signal_handler_raises_for_finally(sig):
    with pytest.raises(demo.CheckFailed):
        demo.interrupted(sig, None)


@pytest.fixture
def signed(data):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid=data['kid'], use='sig', alg='RS256')
    def sign(customer_id, **overrides):
        now = int(time.time())
        claims = dict(iss=data['issuer'], aud=data['audience'], sub=customer_id,
                      role='CUSTOMER', iat=now, nbf=now, exp=now + 900)
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid': data['kid']})
    return sign, {'keys': [public]}


@pytest.mark.parametrize('overrides', [{'role': 'ADMIN'}, {'aud': 'other'}, {'iss': 'https://wrong.test'},
                                     {'exp': 0}, {'nbf': 0}, {'cpf': 'forbidden'},
                                     {'exp': int(time.time()) + 1000}])
def test_jwt_validation_is_cryptographic_and_strict(data, signed, overrides):
    sign, jwks = signed
    with pytest.raises((demo.CheckFailed, jwt.PyJWTError)):
        demo.verify_token(sign(data['owner']['customer_id'], **overrides), data,
                          data['owner']['customer_id'], jwks)


def test_wrong_key_is_rejected(data, signed):
    sign, jwks = signed
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks['keys'][0].update(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(wrong.public_key())))
    with pytest.raises(jwt.InvalidSignatureError):
        demo.verify_token(sign(data['owner']['customer_id']), data, data['owner']['customer_id'], jwks)


@pytest.mark.parametrize('admin_status', [403, 401, 200])
def test_full_scenarios_real_handler_and_signing_fake_transports(data, signed, capsys, admin_status):
    sign, jwks = signed
    lookup = Mock()
    def find(cpf_value):
        return [Identity(data[n]['customer_id'], n != 'inactive') for n in
                ('owner', 'other', 'inactive') if data[n]['cpf'] == cpf_value]
    lookup.find.side_effect = find
    service = AuthenticationService(lookup, SimpleNamespace(sign=sign))
    lam = Mock()
    def invoke(**kwargs):
        assert kwargs['InvocationType'] == 'RequestResponse' and kwargs['LogType'] == 'None'
        response = handle(json.loads(kwargs['Payload']), lambda: service)
        return dict(StatusCode=200, ExecutedVersion='$LATEST',
                    Payload=io.BytesIO(json.dumps(response).encode()))
    lam.invoke.side_effect = invoke
    def get(url, token=None):
        if url == data['jwks_url']:
            return 200, json.dumps(jwks).encode()
        if '/admin/' in url:
            return admin_status, b'{}'
        try:
            owner = data['owner'] if url.endswith(data['owner']['order_id']) else data['other']
            demo.verify_token(token, data, owner['customer_id'], jwks)
            return 200, json.dumps({'id': owner['order_id']}).encode()
        except demo.CheckFailed:
            return 404, b'{}'
        except (jwt.PyJWTError, TypeError):
            return 401, b'{}'
    evidence = demo.Evidence()
    demo.scenarios(lam, data, evidence, get=get)
    assert evidence.failed == (admin_status != 403)
    assert lam.invoke.call_count == 5
    output = capsys.readouterr().out
    for value in (data['owner']['cpf'], data['other']['cpf'], data['owner']['customer_id'], 'access_token'):
        assert value not in output
    assert 'java_foreign_order_hidden' in output


def test_database_uses_reader_tls_params_and_never_writes(data):
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    cursor = Mock()
    cursor.fetchone.side_effect = [('on',), (True,), (True,)]
    cursor.fetchall.side_effect = [[(data[n]['customer_id'], active)] for n, active in
                                  [('owner', True), ('other', True), ('inactive', False)]] + [[]]
    conn.execute.return_value = cursor
    connect = Mock(return_value=conn)
    demo.audit_database(data, 'never-print-password', demo.Evidence(), connect=connect)
    args = connect.call_args.kwargs
    assert args['user'] == 'oficina_lambda' and args['sslmode'] == 'verify-full'
    assert args['connect_timeout'] == 3 and 'read_only=on' in args['options']
    assert all(c.args[0].lstrip().split()[0] in ('SELECT', 'SHOW') for c in conn.execute.call_args_list)
    for call in conn.execute.call_args_list[-4:]:
        assert '%s' in call.args[0] and len(call.args[1]) == 1
    conn.rollback.assert_called_once()


def test_invalid_synthetic_inputs_rejected(data):
    for name, value in [('synthetic_only', False), ('missing_cpf', data['owner']['cpf']),
                        ('db_host', 'localhost'), ('version', 'alias')]:
        bad = copy.deepcopy(data)
        bad[name] = value
        with pytest.raises(demo.CheckFailed):
            demo.validate_inputs(bad)


@pytest.mark.parametrize('condition', ['ci', 'no_tty', 'no_recovery', 'wrong_phrase'])
def test_all_operator_gates_precede_aws(monkeypatch, condition):
    factory = Mock()
    monkeypatch.setattr(demo, 'clients', factory)
    monkeypatch.setattr(demo.sys.stdin, 'isatty', lambda: condition != 'no_tty')
    monkeypatch.setattr(demo.getpass, 'getpass', lambda _: 'wrong' if condition == 'wrong_phrase'
                        else 'AUTORIZO SOMENTE STAGING SINTETICO')
    monkeypatch.delenv('CI', raising=False)
    if condition == 'ci':
        monkeypatch.setenv('CI', 'true')
    args = ['window', '--authorized']
    if condition != 'no_recovery':
        args.append('--recovery-armed')
    assert demo.main(args) == 1
    factory.assert_not_called()


@pytest.mark.parametrize('override', ['AWS_ACCESS_KEY_ID', 'AWS_ENDPOINT_URL_LAMBDA'])
def test_aws_overrides_rejected_before_sdk_import(monkeypatch, override):
    monkeypatch.setenv(override, 'untrusted')
    with pytest.raises(demo.CheckFailed, match='aws_environment_override'):
        demo.clients()


@pytest.mark.parametrize('bad_account', [False, True])
def test_sdk_pins_profile_region_endpoint_settings(monkeypatch, bad_account):
    import sys
    for key in list(demo.os.environ):
        if key.startswith('AWS_'):
            monkeypatch.delenv(key)
    sts = Mock()
    sts.get_caller_identity.return_value = dict(Account='wrong' if bad_account else demo.ACCOUNT,
        Arn=f'arn:aws:sts::{demo.ACCOUNT}:assumed-role/test/session')
    session = Mock()
    session.client.return_value = sts
    factory, cfg = Mock(return_value=session), Mock()
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(Session=factory))
    monkeypatch.setitem(sys.modules, 'botocore.config', SimpleNamespace(Config=cfg))
    if bad_account:
        with pytest.raises(demo.CheckFailed):
            demo.clients()
    else:
        demo.clients()
    factory.assert_called_once_with(profile_name=demo.PROFILE, region_name=demo.REGION)
    assert cfg.call_args.kwargs['ignore_configured_endpoint_urls'] is True
    assert cfg.call_args.kwargs['retries'] == {'total_max_attempts': 1}


@pytest.mark.parametrize('fault', ['function_error', 'transport', 'version', 'oversized', 'malformed'])
def test_lambda_transport_failures_never_count_as_issuance(fault):
    response = dict(StatusCode=200, ExecutedVersion='$LATEST')
    payload = b'{}'
    if fault == 'function_error':
        response['FunctionError'] = 'Unhandled'
    elif fault == 'transport':
        response['StatusCode'] = 202
    elif fault == 'version':
        response['ExecutedVersion'] = '999'
    elif fault == 'oversized':
        payload = b'x' * (demo.LIMIT + 1)
    else:
        payload = b'sensitive error payload'
    stream = io.BytesIO(payload)
    response['Payload'] = stream
    lam = Mock(invoke=Mock(return_value=response))
    with pytest.raises((demo.CheckFailed, ValueError)):
        demo.invoke(lam, 'synthetic', '$LATEST')
    assert stream.closed


def test_http_does_not_follow_redirect_or_forward_token(monkeypatch):
    response = Mock(status=302, read=Mock(return_value=b''))
    conn = Mock(getresponse=Mock(return_value=response))
    factory = Mock(return_value=conn)
    monkeypatch.setattr(demo.http.client, 'HTTPSConnection', factory)
    assert demo.http_get('https://java.test/path', 'sensitive-token') == (302, b'')
    factory.assert_called_once()
    assert factory.call_args.kwargs['timeout'] == 5
    conn.request.assert_called_once()
    conn.close.assert_called_once()


def test_cli_scrubs_unexpected_exception(monkeypatch, capsys):
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.setattr(demo.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(demo.getpass, 'getpass', lambda _: 'AUTORIZO SOMENTE STAGING SINTETICO')
    monkeypatch.setattr(demo.resource, 'setrlimit', Mock())
    monkeypatch.setattr(demo.logging, 'disable', Mock())
    monkeypatch.setattr(demo, 'clients', Mock(side_effect=RuntimeError('password cpf token private_key')))
    assert demo.main(['preflight', '--authorized']) == 1
    output = capsys.readouterr()
    assert output.err == '' and 'private_key' not in output.out and 'password' not in output.out


def test_watchdog_interruption_closes_without_opening(monkeypatch):
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.setattr(demo.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(demo.getpass, 'getpass', lambda _: 'AUTORIZO SOMENTE STAGING SINTETICO')
    monkeypatch.setattr(demo.resource, 'setrlimit', Mock())
    monkeypatch.setattr(demo.logging, 'disable', Mock())
    lam = Mock()
    lam.get_function_concurrency.return_value = dict(ReservedConcurrentExecutions=0)
    monkeypatch.setattr(demo, 'clients', lambda: {'lambda': lam})
    monkeypatch.setattr(demo.time, 'sleep', Mock(side_effect=demo.CheckFailed('interrupted_or_deadline')))
    assert demo.main(['watchdog', '--authorized']) == 1
    lam.put_function_concurrency.assert_called_once_with(
        FunctionName=demo.FUNCTION, ReservedConcurrentExecutions=0)


def test_elapsed_consent_or_credentials_cannot_open_after_deadline(monkeypatch, data, capsys):
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.setattr(demo.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(demo.getpass, 'getpass', lambda _: 'AUTORIZO SOMENTE STAGING SINTETICO')
    monkeypatch.setattr(demo.resource, 'setrlimit', Mock())
    monkeypatch.setattr(demo.logging, 'disable', Mock())
    monkeypatch.setattr(demo, 'private_json', lambda _: data)
    monkeypatch.setattr(demo, 'preflight', Mock())
    monkeypatch.setattr(demo, 'audit_database', Mock())
    monkeypatch.setattr(demo.time, 'monotonic', Mock(side_effect=[0, 181]))
    lam = Mock()
    monkeypatch.setattr(demo, 'clients', lambda: {'lambda': lam})
    assert demo.main(['window', '--authorized', '--recovery-armed', '--input', 'unused']) == 1
    lam.put_function_concurrency.assert_not_called()
    assert 'window_deadline_before_open' in capsys.readouterr().out


@pytest.mark.parametrize('origin', [
    'http://java.test', 'http://10.0.0.1:8080', 'http://127.0.0.1:8080',
    'http://localhost:18080', 'http://[::1]:18080', 'http://127.1:18080',
    'http://127.0.0.1:18080/', 'http://127.0.0.1:18080@evil.test',
    'http://127.0.0.1:18080?x=1', 'http://127.0.0.1:18080#fragment',
])
def test_private_adapter_rejects_alternate_origin_even_when_attested(data, origin):
    data.update(java_transport='ssm-loopback', java_url=origin, ssm_tunnel_attested=True)
    with pytest.raises(demo.CheckFailed):
        demo.validate_inputs(data)


@pytest.mark.parametrize('missing_gate', ['transport', 'attestation'])
def test_private_adapter_requires_explicit_gates(data, missing_gate):
    data.update(java_url=demo.JAVA_SSM_ORIGIN, java_transport='ssm-loopback',
                ssm_tunnel_attested=True)
    data.pop('java_transport' if missing_gate == 'transport' else 'ssm_tunnel_attested')
    with pytest.raises(demo.CheckFailed):
        demo.validate_inputs(data)


def test_private_adapter_does_not_relax_jwks_https(data):
    data.update(java_url=demo.JAVA_SSM_ORIGIN, java_transport='ssm-loopback',
                ssm_tunnel_attested=True, jwks_url='http://127.0.0.1:18080/keys')
    with pytest.raises(demo.CheckFailed, match='trusted_https_url'):
        demo.validate_inputs(data)


@pytest.mark.parametrize('path', [
    '/api/v1/admin/work-orders?cpf=x', '/api/v1/admin/work-orders#x',
    '/api/v1/admin/work-orders/../customers', '/actuator/env',
    '/api/v1/customer/work-orders/invalid', '//evil.test',
])
def test_private_adapter_only_allows_exact_smoke_gets(data, monkeypatch, path):
    data.update(java_url=demo.JAVA_SSM_ORIGIN, java_transport='ssm-loopback',
                ssm_tunnel_attested=True)
    factory = Mock()
    monkeypatch.setattr(demo.http.client, 'HTTPConnection', factory)
    with pytest.raises(demo.CheckFailed):
        demo.java_get(data, demo.JAVA_SSM_ORIGIN + path, 'never-sent')
    factory.assert_not_called()


@pytest.mark.parametrize('status', [200, 302, 401, 403, 404])
def test_private_adapter_fixed_loopback_does_not_follow_redirects(data, monkeypatch, status):
    data.update(java_url=demo.JAVA_SSM_ORIGIN, java_transport='ssm-loopback',
                ssm_tunnel_attested=True)
    demo.validate_inputs(data)
    response = Mock(status=status, read=Mock(return_value=b'{}'))
    conn = Mock(getresponse=Mock(return_value=response))
    factory = Mock(return_value=conn)
    monkeypatch.setattr(demo.http.client, 'HTTPConnection', factory)
    monkeypatch.setenv('HTTP_PROXY', 'http://untrusted.test:8080')
    path = '/api/v1/customer/work-orders/' + data['owner']['order_id']
    assert demo.java_get(data, demo.JAVA_SSM_ORIGIN + path, 'secret') == (status, b'{}')
    factory.assert_called_once_with('127.0.0.1', 18080, timeout=5)
    conn.request.assert_called_once_with('GET', path, headers={
        'Accept': 'application/json', 'Authorization': 'Bearer secret'})
    conn.close.assert_called_once()


def test_https_transport_keeps_certificate_hostname_validation(monkeypatch):
    response = Mock(status=200, read=Mock(return_value=b'{}'))
    factory = Mock(return_value=Mock(getresponse=Mock(return_value=response)))
    monkeypatch.setattr(demo.http.client, 'HTTPSConnection', factory)
    demo.http_get('https://jwks.test/keys')
    context = factory.call_args.kwargs['context']
    assert context.check_hostname is True
    assert context.verify_mode == demo.ssl.CERT_REQUIRED


def test_rds_tunnel_preserves_certificate_hostname_and_reader(data):
    data.update(db_transport='ssm-loopback', ssm_tunnel_attested=True)
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    cursor = Mock()
    cursor.fetchone.side_effect = [('on',), (True,), (True,)]
    cursor.fetchall.side_effect = [[(data[n]['customer_id'], active)] for n, active in
                                  [('owner', True), ('other', True), ('inactive', False)]] + [[]]
    conn.execute.return_value = cursor
    connect = Mock(return_value=conn)
    demo.audit_database(data, 'never-print-password', demo.Evidence(), connect=connect)
    assert connect.call_args.kwargs['host'] == data['db_host']
    assert connect.call_args.kwargs['hostaddr'] == '127.0.0.1'
    assert connect.call_args.kwargs['port'] == 15432
    assert connect.call_args.kwargs['sslmode'] == 'verify-full'
    assert connect.call_args.kwargs['sslrootcert'] == data['db_ca_file']
    assert connect.call_args.kwargs['user'] == 'oficina_lambda'
    conn.rollback.assert_called_once()


def test_unknown_transport_and_unattested_db_tunnel_rejected(data):
    data['java_transport'] = 'http'
    with pytest.raises(demo.CheckFailed, match='java_transport'):
        demo.validate_inputs(data)
    del data['java_transport']
    data['db_transport'] = 'ssm-loopback'
    with pytest.raises(demo.CheckFailed, match='ssm_tunnel_attestation'):
        demo.validate_inputs(data)


def test_cleanup_has_independent_deadline_and_never_claims_zero_on_expiry(monkeypatch, capsys):
    alarm = Mock()
    monkeypatch.setattr(demo.signal, 'alarm', alarm)
    lam = Mock()
    lam.put_function_concurrency.side_effect = demo.CleanupDeadline()
    evidence = demo.Evidence()
    with pytest.raises(demo.CheckFailed, match='cleanup_unconfirmed_use_independent_recovery'):
        demo.close_window(lam, evidence, sleep=Mock())
    assert evidence.failed
    assert [call.args for call in alarm.call_args_list] == [(0,), (120,), (0,)]
    assert signal.getsignal(signal.SIGALRM) is demo.cleanup_expired
    lam.get_function_concurrency.assert_not_called()
    assert 'PASS' not in capsys.readouterr().out


def test_cleanup_alarm_escapes_sdk_exception_handlers():
    with pytest.raises(demo.CleanupDeadline):
        demo.cleanup_expired(signal.SIGALRM, None)


def test_owner_scope_requires_only_a_and_keeps_full_validation(data, clients):
    owner_data = {k: v for k, v in data.items() if k not in ('other', 'inactive', 'missing_cpf')}
    owner_data.update(owner_order_confirmed=True, admin_trust_ready=True, provenance_ref='private-record')
    demo.validate_owner_inputs(owner_data)
    env = clients['lambda'].get_function_configuration.return_value['Environment']['Variables']
    env['AUTH_SYNTHETIC_CUSTOMER_IDS'] = json.dumps([data['owner']['customer_id']])
    demo.preflight(clients, owner_data, demo.Evidence(), identity_names=('owner',))
    with pytest.raises(KeyError):
        demo.validate_inputs(owner_data)
    with pytest.raises(demo.CheckFailed, match='synthetic_allowlist'):
        demo.preflight(clients, data, demo.Evidence())


@pytest.mark.parametrize('admin_status', [403, 401])
def test_owner_only_real_signature_three_exact_gets(data, signed, monkeypatch, capsys, admin_status):
    sign, jwks = signed
    token = sign(data['owner']['customer_id'])
    invoke = Mock(return_value=(200, dict(access_token=token, token_type='Bearer', expires_in=900)))
    monkeypatch.setattr(demo, 'invoke', invoke)
    monkeypatch.setattr(demo, 'http_get', Mock(return_value=(200, json.dumps(jwks).encode())))
    get = Mock(side_effect=[(200, json.dumps({'id': data['owner']['order_id']}).encode()),
                            (401, b'{}'), (admin_status, b'{}')])
    monkeypatch.setattr(demo, 'java_get', get)
    evidence = demo.Evidence()
    demo.owner_scenarios(Mock(), data, evidence)
    invoke.assert_called_once()
    assert get.call_count == 3
    assert get.call_args_list[0].args[1] == get.call_args_list[1].args[1]
    assert get.call_args_list[1].args[2] is None
    assert get.call_args_list[2].args[1].endswith('/api/v1/admin/work-orders')
    assert evidence.failed == (admin_status != 403)
    output = capsys.readouterr().out
    assert token not in output and data['owner']['cpf'] not in output
    assert data['owner']['customer_id'] not in output and data['owner']['order_id'] not in output


def test_owner_only_wrong_issuer_stops_before_java(data, signed, monkeypatch):
    sign, jwks = signed
    monkeypatch.setattr(demo, 'http_get', Mock(return_value=(200, json.dumps(jwks).encode())))
    monkeypatch.setattr(demo, 'invoke', Mock(return_value=(200, dict(
        access_token=sign(data['owner']['customer_id'], iss='https://wrong.test'),
        token_type='Bearer', expires_in=900))))
    get = Mock()
    monkeypatch.setattr(demo, 'java_get', get)
    with pytest.raises(jwt.InvalidIssuerError):
        demo.owner_scenarios(Mock(), data, demo.Evidence())
    get.assert_not_called()


def test_owner_database_checks_only_a_readonly(data):
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.execute.return_value.fetchone.side_effect = [('on',), (True,), (True,)]
    conn.execute.return_value.fetchall.return_value = [(data['owner']['customer_id'], True)]
    demo.audit_database(data, 'private', demo.Evidence(), connect=Mock(return_value=conn), owner_only=True)
    assert conn.execute.call_count == 4
    assert conn.execute.call_args.args[1] == (data['owner']['cpf'],)
    conn.rollback.assert_called_once()
