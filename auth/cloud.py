"""Secrets Manager adapter for closed, synthetic-only staging. No secret in env/ZIP/state."""
import json
import os
import re
import ssl
import tempfile
from uuid import UUID

from psycopg.conninfo import make_conninfo

from auth.jwt import RS256Signer
from auth.postgres import PostgresIdentityLookup
from auth.service import AuthenticationService


class MemoryPrivateKeyProvider:
    def __init__(self, pem):
        self.pem = pem

    def load(self):
        return self.pem.encode('ascii')


class SyntheticIdentityLookup:
    def __init__(self, delegate, allowed):
        self.delegate, self.allowed = delegate, allowed

    def find(self, cpf):
        rows = self.delegate.find(cpf)
        # Preserve duplicate detection. A non-allowed row never becomes a usable identity.
        if len(rows) != 1 or str(rows[0].customer_id) not in self.allowed:
            return []
        return rows


def build_cloud_service(client=None):
    if os.environ.get('APP_ENV') != 'staging':
        raise ValueError('Synthetic staging only')
    ids = json.loads(os.environ['AUTH_SYNTHETIC_CUSTOMER_IDS'])
    if not isinstance(ids, list) or not ids or any(str(UUID(value)) != value for value in ids):
        raise ValueError('Synthetic UUID allowlist required')
    host = os.environ['DB_HOST']
    if (not re.fullmatch(r'[A-Za-z0-9.-]+\.us-east-2\.rds\.amazonaws\.com', host)
            or os.environ['DB_NAME'] != 'oficina_db' or os.environ['DB_PORT'] != '5432'):
        raise ValueError('RDS contract mismatch')
    if not os.environ['JWT_ISSUER'].startswith('https://'):
        raise ValueError('HTTPS issuer required')
    if client is None:
        # boto3 is supplied by the pinned/tested managed Lambda runtime, not a network download.
        import boto3
        from botocore.config import Config
        client = boto3.client('secretsmanager', region_name='us-east-2',
                              config=Config(connect_timeout=1, read_timeout=2,
                                            retries={'total_max_attempts': 1}))
    response = client.get_secret_value(SecretId=os.environ['AUTH_SECRET_ARN'], VersionStage='AWSCURRENT')
    data = json.loads(response['SecretString'])
    if set(data) != {'username', 'password', 'private_key_pem', 'ca_pem', 'kid'}:
        raise ValueError('Unexpected secret schema')
    if data['username'] != 'oficina_lambda' or data['kid'] != os.environ['JWT_KID']:
        raise ValueError('Secret identity/key mismatch')
    if any(not isinstance(value, str) or not value.strip() for value in data.values()):
        raise ValueError('Empty secret field')
    # Validate PEM certificates before persisting only the public CA. Never persist private key/DSN.
    ssl.create_default_context(cadata=data['ca_pem'])
    signer = RS256Signer(MemoryPrivateKeyProvider(data['private_key_pem']),
                         os.environ['JWT_ISSUER'], os.environ['JWT_AUDIENCE'], data['kid'])
    descriptor, path = tempfile.mkstemp(prefix='auth-rds-ca-', suffix='.crt')
    with os.fdopen(descriptor, 'w') as target:
        target.write(data['ca_pem'])
    dsn = make_conninfo(host=host, port=5432, dbname='oficina_db', user='oficina_lambda',
                        password=data['password'], sslmode='verify-full', sslrootcert=path)
    service = AuthenticationService(SyntheticIdentityLookup(PostgresIdentityLookup(dsn), set(ids)), signer)
    service.ca_file = path
    return service
