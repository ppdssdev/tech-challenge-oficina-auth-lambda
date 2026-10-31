"""Run inside the official runtime with ONLY the extracted bundle on PYTHONPATH."""
import json
import sys
from uuid import uuid4
from unittest.mock import Mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from auth.handler import handle, lambda_handler
from auth.jwt import RS256Signer
from auth.ports import Identity
from auth.service import AuthenticationService

assert sys.version_info[:2] == (3, 13)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption())
signer = RS256Signer(Mock(load=Mock(return_value=pem)), 'https://test.invalid', 'test', 'smoke')
service = AuthenticationService(Mock(find=Mock(return_value=[Identity(uuid4(), True)])), signer)
event = dict(version='2.0', rawPath='/auth/token', rawQueryString='',
             requestContext={'http': {'method': 'POST', 'path': '/auth/token'}},
             headers={'content-type': 'application/json'}, isBase64Encoded=False,
             body=json.dumps({'cpf': '52998224725'}))
response = handle(event, lambda: service)
assert response['statusCode'] == 200
claims = jwt.decode(json.loads(response['body'])['access_token'], key.public_key(),
                    algorithms=['RS256'], issuer='https://test.invalid', audience='test')
assert claims['role'] == 'CUSTOMER' and claims['exp'] - claims['iat'] == 900
assert lambda_handler({}, None)['statusCode'] == 400
print('Python 3.13 Linux: handler imported, native dependencies loaded, RS256 verified')
