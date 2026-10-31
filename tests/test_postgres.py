import os
from uuid import uuid4
from unittest.mock import Mock

import psycopg
import pytest

from auth.errors import AuthenticationFailed
from auth.postgres import PostgresIdentityLookup
from auth.service import AuthenticationService

DSN = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DSN, reason='TEST_DATABASE_URL required: real PostgreSQL')


def test_real_postgres():
    # Dedicated disposable database; domain/view DDL comes from the app PR in CI.
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute('TRUNCATE public.customers CASCADE')
        for cpf, active in [('52998224725', True), ('12345678909', False)]:
            connection.execute("""INSERT INTO public.customers
                (id, full_name, document_type, document_number, active, created_at, updated_at)
                VALUES (%s, 'Synthetic', 'CPF', %s, %s, now(), now())""",
                (uuid4(), cpf, active))
        adapter = PostgresIdentityLookup(DSN)
        assert adapter.find('52998224725')[0].active is True
        assert adapter.find('12345678909')[0].active is False
        assert adapter.find('00000000000') == []
        assert adapter.find("' OR 1=1 --") == []
        connection.execute("""INSERT INTO public.customers
            (id, full_name, document_type, document_number, active, created_at, updated_at)
            VALUES (%s, 'Synthetic', 'CPF', '529.982.247-25', false, now(), now())""", (uuid4(),))
        assert len(adapter.find('52998224725')) == 2
        signer = Mock()
        with pytest.raises(AuthenticationFailed):
            AuthenticationService(adapter, signer).authenticate('52998224725')
        signer.sign.assert_not_called()
        # View permission works under the capability role; base tables remain inaccessible.
        with connection.transaction():
            connection.execute('SET LOCAL ROLE auth_readonly')
            assert connection.execute('SELECT count(*) FROM auth.customer_identity_v1').fetchone()[0] == 3
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute('SELECT * FROM public.customers')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute("UPDATE auth.customer_identity_v1 SET active = true")


def test_real_connection_failure():
    with pytest.raises(psycopg.OperationalError):
        PostgresIdentityLookup('host=127.0.0.1 port=1 dbname=missing sslmode=disable').find('52998224725')


def test_real_readonly_login_and_handler(tmp_path, monkeypatch):
    import json
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from auth.handler import build_service, handle
    from test_runtime import event

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / 'disposable.key'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute('TRUNCATE public.customers CASCADE')
        customer_id = uuid4()
        connection.execute("""INSERT INTO public.customers
            (id, full_name, document_type, document_number, active, created_at, updated_at)
            VALUES (%s, 'Synthetic', 'CPF', '52998224725', true, now(), now())""", (customer_id,))
        connection.execute("CREATE ROLE test_auth_login LOGIN PASSWORD 'disposable-test-only' INHERIT")
        try:
            connection.execute('GRANT auth_readonly TO test_auth_login')
            params = conninfo_to_dict(DSN) | {'user': 'test_auth_login', 'password': 'disposable-test-only'}
            for name, value in dict(APP_ENV='local', JWT_ISSUER='https://issuer.test',
                    JWT_AUDIENCE='oficina-api-test', JWT_KID='integration',
                    JWT_PRIVATE_KEY_FILE=str(path), DATABASE_URL=make_conninfo(**params)).items():
                monkeypatch.setenv(name, value)
            monkeypatch.delenv('AWS_LAMBDA_FUNCTION_NAME', raising=False)
            response = handle(event(), build_service)
            assert response['statusCode'] == 200
            claims = jwt.decode(json.loads(response['body'])['access_token'], key.public_key(),
                                algorithms=['RS256'], issuer='https://issuer.test',
                                audience='oficina-api-test')
            assert claims['sub'] == str(customer_id)
            assert claims['role'] == 'CUSTOMER'
            connection.execute('UPDATE public.customers SET active=false')
            assert handle(event(), build_service)['statusCode'] == 401
            connection.execute('DELETE FROM public.customers')
            assert handle(event(), build_service)['statusCode'] == 401
        finally:
            connection.execute('DROP ROLE test_auth_login')
