"""Run inside the official runtime with ONLY the extracted bundle on PYTHONPATH."""
import json
import sys
from uuid import uuid4
from unittest.mock import MagicMock, Mock, patch

import jwt
import boto3
from auth.cloud import build_cloud_service
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from auth.handler import handle, lambda_handler
from auth.jwt import RS256Signer
from auth.ports import Identity
from auth.postgres import PostgresIdentityLookup
from auth.service import AuthenticationService

assert sys.version_info[:2] == (3, 13)
assert callable(boto3.client) and callable(build_cloud_service)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption())
signer = RS256Signer(Mock(load=Mock(return_value=pem)), 'https://test.invalid', 'test', 'smoke')
customer_id = uuid4()
connection = MagicMock()
cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
cursor.fetchall.return_value = [(customer_id, True)]
adapter = PostgresIdentityLookup('host=unused.invalid dbname=oficina_db')
with patch('auth.postgres.psycopg.connect', return_value=connection):
    assert adapter.find('52998224725') == [Identity(customer_id, True)]
cursor.execute.assert_called_once_with(
    'SELECT customer_id, active FROM auth.customer_identity_v2 WHERE cpf = %s LIMIT 2',
    ('52998224725',))
service = AuthenticationService(Mock(find=Mock(return_value=[Identity(customer_id, True)])), signer)
event = dict(version='2.0', rawPath='/auth/token', rawQueryString='',
             requestContext={'http': {'method': 'POST', 'path': '/auth/token'}},
             headers={'content-type': 'application/json'}, isBase64Encoded=False,
             body=json.dumps({'cpf': '52998224725'}))
response = handle(event, lambda: service)
assert response['statusCode'] == 200
claims = jwt.decode(json.loads(response['body'])['access_token'], key.public_key(),
                    algorithms=['RS256'], issuer='https://test.invalid', audience='test')
assert claims['role'] == 'CUSTOMER' and claims['exp'] - claims['iat'] == 900
assert claims['sub'] == str(customer_id) and claims['nbf'] == claims['iat']
assert set(claims) == {'iss', 'aud', 'sub', 'role', 'iat', 'nbf', 'exp'}
assert jwt.get_unverified_header(json.loads(response['body'])['access_token']) == {
    'alg': 'RS256', 'kid': 'smoke', 'typ': 'JWT'}
assert lambda_handler({}, None)['statusCode'] == 400
print('Python 3.13 Linux: bundle V2 query checked, native dependencies loaded, RS256 verified')
