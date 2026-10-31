import base64
import json
import logging
from unittest.mock import Mock
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives import serialization

from auth.config import Config
from auth.handler import handle
from auth.jwt import RS256Signer
from auth.ports import Identity
from auth.service import AuthenticationService
from test_contracts import validator

CPF = '52998224725'  # Synthetic test input only; never logged.
SUB = UUID('00000000-0000-4000-8000-000000000001')


def event(body=None):
    return dict(version='2.0', rawPath='/auth/token', rawQueryString='',
                requestContext={'http': {'method': 'POST', 'path': '/auth/token'}},
                headers={'Content-Type': 'application/json'}, isBase64Encoded=False,
                body=json.dumps({'cpf': CPF}) if body is None else body)


@pytest.fixture
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return RS256Signer(Mock(load=Mock(return_value=pem)), 'https://issuer.test',
                       'oficina-api-test', 'test-key')


def invoke(signer, rows=None, request=None, failure=None):
    lookup = Mock()
    lookup.find.return_value = rows if rows is not None else [Identity(SUB, True)]
    lookup.find.side_effect = failure
    service = AuthenticationService(lookup, signer)
    response = handle(event() if request is None else request, lambda: service)
    validator('response' if response['statusCode'] == 200 else 'error').validate(
        json.loads(response['body']))
    assert response['headers']['Cache-Control'] == 'no-store'
    assert response['headers']['Content-Type'] == 'application/json'
    return response, lookup


def test_valid_token(signer, caplog):
    caplog.set_level(logging.DEBUG)
    response, lookup = invoke(signer)
    assert response['statusCode'] == 200
    lookup.find.assert_called_once_with(CPF)
    token = json.loads(response['body'])['access_token']
    claims = jwt.decode(token, signer.key.public_key(), algorithms=['RS256'],
                        audience='oficina-api-test', issuer='https://issuer.test',
                        options={'require': ['iss', 'aud', 'sub', 'role', 'iat', 'nbf', 'exp']})
    assert set(claims) == {'iss', 'aud', 'sub', 'role', 'iat', 'nbf', 'exp'}
    assert claims['sub'] == str(SUB) and claims['role'] == 'CUSTOMER'
    assert claims['exp'] - claims['iat'] == 900 and claims['nbf'] == claims['iat']
    assert jwt.get_unverified_header(token) == {'alg': 'RS256', 'kid': 'test-key', 'typ': 'JWT'}
    assert CPF not in caplog.text and token not in caplog.text
    for args in ({'audience': 'wrong', 'issuer': 'https://issuer.test'},
                 {'audience': 'oficina-api-test', 'issuer': 'wrong'}):
        with pytest.raises(jwt.InvalidTokenError):
            jwt.decode(token, signer.key.public_key(), algorithms=['RS256'], **args)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, other.public_key(), algorithms=['RS256'], audience='oficina-api-test')
    with pytest.raises(jwt.InvalidAlgorithmError):
        jwt.decode(token, signer.key.public_key(), algorithms=['HS256'])


@pytest.mark.parametrize('rows', [[], [Identity(SUB, False)],
                                  [Identity(SUB, True), Identity(SUB, True)],
                                  [Identity(SUB, True), Identity(SUB, False)]])
def test_denied(rows, signer):
    signer.sign = Mock(side_effect=AssertionError('must not sign'))
    response, _ = invoke(signer, rows=rows)
    assert response['statusCode'] == 401
    signer.sign.assert_not_called()


@pytest.mark.parametrize('body', ['{', '{}', 'null', '[]', '{"cpf":123}',
    '{"cpf":"11111111111"}', '{"cpf":"52998224724"}', '{"cpf":"529.982.247-25"}',
    '{"cpf":"52998224725","role":"ADMIN"}', ' ' * 1025,
    '{"cpf":"52998224725","cpf":"52998224725"}', '{"cpf":"１２３４５６７８９０９"}'])
def test_bad_body(body, signer):
    response, lookup = invoke(signer, request=event(body))
    assert response['statusCode'] == 400
    lookup.find.assert_not_called()


@pytest.mark.parametrize('change', [
    {'version': '1.0'}, {'rawPath': '/other'}, {'requestContext': {}},
    {'requestContext': {'http': {'method': 'GET', 'path': '/auth/token'}}},
    {'rawQueryString': 'cpf=52998224725'}, {'headers': {}}, {'body': None},
    {'headers': {'content-type': 'text/plain'}}, {'headers': None},
    {'isBase64Encoded': 'false'}, {'isBase64Encoded': True, 'body': '!!'},
    {'queryStringParameters': {'cpf': CPF}},
    {'isBase64Encoded': True, 'body': base64.b64encode(b' ' * 1025).decode()},
    {'isBase64Encoded': True, 'body': base64.b64encode(b'\xff').decode()},
])
def test_bad_event(change, signer):
    response, lookup = invoke(signer, request=event() | change)
    assert response['statusCode'] == 400
    lookup.find.assert_not_called()


def test_base64_and_boundary(signer):
    request = event()
    request['body'] += ' ' * (1024 - len(request['body']))
    assert invoke(signer, request=request)[0]['statusCode'] == 200
    request['body'] = base64.b64encode(request['body'].encode()).decode()
    request['isBase64Encoded'] = True
    assert invoke(signer, request=request)[0]['statusCode'] == 200


def test_failure_logs_safe(signer, caplog):
    response, _ = invoke(signer, failure=RuntimeError(CPF + ' SECRET Authorization token'))
    assert response['statusCode'] == 503
    assert CPF not in caplog.text + response['body']
    assert 'SECRET' not in caplog.text + response['body']
    assert 'Traceback' not in caplog.text


def test_missing_config(monkeypatch):
    monkeypatch.delenv('JWT_ISSUER', raising=False)
    with pytest.raises(ValueError):
        Config.from_env()
    response = handle(event(), lambda: Config.from_env())
    assert response['statusCode'] == 503


@pytest.mark.parametrize('key', [b'invalid', b'', None])
def test_bad_key(key):
    with pytest.raises((ValueError, TypeError)):
        RS256Signer(Mock(load=Mock(return_value=key)), 'issuer', 'audience', 'kid')


def test_wrong_key_type_and_weak_key():
    for key in (ec.generate_private_key(ec.SECP256R1()),
                rsa.generate_private_key(public_exponent=65537, key_size=1024)):
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        with pytest.raises(ValueError):
            RS256Signer(Mock(load=Mock(return_value=pem)), 'issuer', 'audience', 'kid')


@pytest.mark.parametrize('field', ['issuer', 'audience', 'kid'])
def test_missing_signer_config(field):
    config = dict(issuer='issuer', audience='audience', kid='kid')
    config[field] = ''
    with pytest.raises(ValueError):
        RS256Signer(Mock(), **config)


def test_cloud_requires_tls(monkeypatch, tmp_path):
    for key, value in dict(APP_ENV='staging', JWT_ISSUER='https://issuer.test',
        JWT_AUDIENCE='oficina-api-staging', JWT_KID='key', JWT_PRIVATE_KEY_FILE='/missing',
        DATABASE_URL='host=localhost sslmode=disable').items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Config.from_env()
    ca = tmp_path / 'ca.pem'
    ca.write_text('test fixture')
    monkeypatch.setenv('DATABASE_URL', f'host=db.test sslmode=verify-full sslrootcert={ca}')
    assert Config.from_env().local is False
    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('AWS_LAMBDA_FUNCTION_NAME', 'test')
    monkeypatch.setenv('DATABASE_URL', 'host=localhost sslmode=disable')
    with pytest.raises(ValueError):
        Config.from_env()


def test_public_jwks_fixture():
    from pathlib import Path
    fixture = json.loads(Path('contracts/jwks.public.json').read_text())
    key = jwt.PyJWKSet.from_dict(fixture).keys[0]
    assert key.algorithm_name == 'RS256'
    assert not set(fixture['keys'][0]) & {'d', 'p', 'q', 'dp', 'dq', 'qi'}


def test_signing_failure_is_generic(signer, caplog):
    signer.sign = Mock(side_effect=ValueError('private key secret'))
    response, _ = invoke(signer)
    assert response['statusCode'] == 503
    assert 'private key' not in response['body'] + caplog.text


def test_expired_and_future_tokens(signer):
    import time
    for timestamp, error in [(time.time() - 901, jwt.ExpiredSignatureError),
                             (time.time() + 120, jwt.ImmatureSignatureError)]:
        signer.clock = lambda: timestamp
        token = signer.sign(SUB)
        with pytest.raises(error):
            jwt.decode(token, signer.key.public_key(), algorithms=['RS256'],
                       issuer='https://issuer.test', audience='oficina-api-test')
