"""Real V11 integration, using the actual staging LOGIN and grants from Java."""
import json
import os
from uuid import UUID, uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from auth.cloud import SyntheticIdentityLookup
from auth.handler import build_service, handle
from auth.postgres import PostgresIdentityLookup
from test_runtime import event

DSN = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DSN, reason='TEST_DATABASE_URL required: real disposable PostgreSQL')


@pytest.fixture(scope='module')
def runtime_dsn():
    params = conninfo_to_dict(DSN)
    assert params['dbname'] == 'oficina_db'
    assert params['host'] in {'localhost', '127.0.0.1', '::1'}
    assert 'hostaddr' not in params
    password = str(uuid4())  # Disposable password; never logged or stored in Git.
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute(sql.SQL('ALTER ROLE oficina_lambda PASSWORD {}').format(sql.Literal(password)))
    yield make_conninfo(**(params | {'user': 'oficina_lambda', 'password': password}))
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute('ALTER ROLE oficina_lambda PASSWORD NULL')


@pytest.fixture
def database(runtime_dsn):
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute('TRUNCATE public.customers CASCADE')
        yield connection, PostgresIdentityLookup(runtime_dsn)
        connection.execute('TRUNCATE public.customers CASCADE')


def customer(connection, cpf='52998224725', document_type='CPF'):
    customer_id = uuid4()
    connection.execute("""INSERT INTO public.customers
        (id, full_name, document_type, document_number, created_at, updated_at)
        VALUES (%s, 'Synthetic', %s, %s, now(), now())""", (customer_id, document_type, cpf))
    return customer_id


def enable(connection, customer_id, active=True):
    connection.execute("""INSERT INTO auth.customer_access (customer_id, active) VALUES (%s, %s)
        ON CONFLICT (customer_id) DO UPDATE SET active = EXCLUDED.active""", (customer_id, active))


def test_v11_explicit_eligibility_and_ambiguous_legacy_cpf(database):
    connection, adapter = database
    customer_id = customer(connection)
    assert adapter.find('52998224725')[0].active is False
    connection.execute('INSERT INTO auth.customer_access (customer_id) VALUES (%s)', (customer_id,))
    assert adapter.find('52998224725')[0].active is False
    enable(connection, customer_id)
    assert adapter.find('52998224725')[0].active is True
    assert adapter.find('00000000000') == []
    assert adapter.find("' OR 1=1 --") == []
    customer(connection, '529.982.247-25')
    # V11 suppresses BOTH identities, rather than returning two rows as old tests assumed.
    assert adapter.find('52998224725') == []
    assert adapter.find('529.982.247-25') == []
    customer(connection, '12345678000195', 'CNPJ')
    assert adapter.find('12345678000195') == []


def test_v11_types_and_business_status_are_independent(database):
    connection, adapter = database
    customer_id = customer(connection)
    columns = connection.execute("""SELECT column_name, data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'auth' AND table_name = 'customer_identity_v2'
        ORDER BY ordinal_position""").fetchall()
    assert columns == [('customer_id', 'uuid', None), ('cpf', 'character varying', 14),
                       ('active', 'boolean', None)]
    # V8 business status defaults true, but does not enable V11 authentication.
    assert connection.execute('SELECT active FROM public.customers').fetchone() == (True,)
    assert adapter.find('52998224725')[0].active is False
    connection.execute('UPDATE public.customers SET active = false')
    enable(connection, customer_id)
    identity = adapter.find('52998224725')[0]
    assert isinstance(identity.customer_id, UUID) and identity.customer_id == customer_id
    assert identity.active is True
    connection.execute('DELETE FROM auth.customer_access')
    assert adapter.find('52998224725')[0].active is False


@pytest.mark.parametrize('legacy', ['529.982.247-25', '52998224725 ', 'x52998224725'])
def test_v11_legacy_documents_never_become_login_identities(database, legacy):
    connection, adapter = database
    enable(connection, customer(connection, legacy))
    assert adapter.find('52998224725') == []
    assert adapter.find(legacy) == []
    enable(connection, customer(connection))
    assert adapter.find('52998224725') == []


def test_runtime_login_has_only_reader_membership_and_no_admin_attributes(runtime_dsn):
    with psycopg.connect(runtime_dsn) as connection:
        assert connection.execute('SELECT current_user, session_user').fetchone() == (
            'oficina_lambda', 'oficina_lambda')
        assert connection.execute("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication,
            rolbypassrls FROM pg_roles WHERE rolname = current_user""").fetchone() == (False,) * 5
        assert connection.execute("""SELECT r.rolname FROM pg_auth_members m
            JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname = current_user""").fetchall() == [('oficina_auth_reader',)]
        assert connection.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname='oficina_auth_reader'").fetchone() == (False,)
        connection.execute('SELECT customer_id, cpf, active FROM auth.customer_identity_v2')
        assert connection.execute("""SELECT has_table_privilege(current_user,
            'auth.customer_identity_v2', 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')""").fetchone() == (False,)
        # Audit effective privileges (including PUBLIC and inherited/column grants),
        # rather than checking only the names of the roles assigned by bootstrap.
        assert connection.execute("""SELECT n.nspname, c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
              AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND c.oid <> 'auth.customer_identity_v2'::regclass
              AND (has_table_privilege(current_user, c.oid,
                   'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(current_user, c.oid,
                   'SELECT,INSERT,UPDATE,REFERENCES'))""").fetchall() == []
        assert connection.execute("""SELECT nspname FROM pg_namespace
            WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
              AND has_schema_privilege(current_user, oid, 'CREATE')""").fetchall() == []
        assert connection.execute("""SELECT has_database_privilege(
            current_user, current_database(), 'CREATE,TEMP')""").fetchone() == (False,)
        assert connection.execute("""SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
              AND c.relkind = 'S'
              AND has_sequence_privilege(current_user, c.oid, 'USAGE,SELECT,UPDATE')""").fetchall() == []


@pytest.mark.parametrize('statement', [
    'SELECT * FROM auth.customer_identity_v1',
    'SELECT * FROM public.customers',
    'SELECT * FROM public.app_users',
    'SELECT * FROM auth.customer_access',
    'UPDATE auth.customer_access SET active = true',
    'INSERT INTO auth.customer_access (customer_id, active) VALUES (gen_random_uuid(), true)',
    'DELETE FROM public.customers',
    'CREATE TABLE public.forbidden (id integer)',
    'CREATE TABLE auth.forbidden (id integer)',
    'CREATE TEMP TABLE forbidden (id integer)',
    'SET ROLE oficina_migrator',
    'SET ROLE oficina_api',
])
def test_runtime_database_permissions_cannot_be_bypassed(runtime_dsn, statement):
    # No read-only client option: prove server grants independently of the adapter.
    with psycopg.connect(runtime_dsn) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)


def test_real_readonly_login_and_synthetic_handler(database, runtime_dsn, tmp_path, monkeypatch):
    connection, _ = database
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / 'disposable.key'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)
    customer_id = customer(connection)
    for name, value in dict(APP_ENV='local', JWT_ISSUER='https://issuer.test',
            JWT_AUDIENCE='oficina-api-test', JWT_KID='integration',
            JWT_PRIVATE_KEY_FILE=str(path), DATABASE_URL=runtime_dsn).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('AWS_LAMBDA_FUNCTION_NAME', raising=False)
    monkeypatch.delenv('AUTH_SECRET_ARN', raising=False)
    service = build_service()
    service.identities = SyntheticIdentityLookup(service.identities, {str(customer_id)})

    def invoke():
        return handle(event(), lambda: service)

    assert invoke()['statusCode'] == 401
    enable(connection, customer_id)
    connection.execute('UPDATE public.customers SET active = false')
    response = invoke()
    assert response['statusCode'] == 200
    claims = jwt.decode(json.loads(response['body'])['access_token'], key.public_key(),
                        algorithms=['RS256'], issuer='https://issuer.test', audience='oficina-api-test')
    assert claims['sub'] == str(customer_id) and claims['role'] == 'CUSTOMER'
    assert claims['exp'] - claims['iat'] == 900 and claims['iat'] == claims['nbf']
    assert set(claims) == {'iss', 'aud', 'sub', 'role', 'iat', 'nbf', 'exp'}
    assert jwt.get_unverified_header(json.loads(response['body'])['access_token']) == {
        'alg': 'RS256', 'kid': 'integration', 'typ': 'JWT'}
    service.identities.allowed = {str(uuid4())}
    assert invoke()['statusCode'] == 401  # SQL eligibility alone is insufficient.
    service.identities.allowed = {str(customer_id)}
    enable(connection, customer_id, False)
    assert invoke()['statusCode'] == 401
    enable(connection, customer_id)
    customer(connection, '529.982.247-25')
    assert invoke()['statusCode'] == 401
    connection.execute('DELETE FROM public.customers')
    assert invoke()['statusCode'] == 401


def test_real_tls_verifies_hostname_and_trust(runtime_dsn, tmp_path):
    params = conninfo_to_dict(runtime_dsn)
    assert params['sslmode'] == 'verify-full', 'Integration DSN must enable real TLS verification'
    with psycopg.connect(runtime_dsn) as connection:
        assert connection.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone() == (True,)
    bad_hostname = make_conninfo(**(params | {'host': 'wrong.invalid', 'hostaddr': '127.0.0.1'}))
    with pytest.raises(psycopg.OperationalError, match='does not match host name'):
        psycopg.connect(bad_hostname)
    from scripts.prepare_test_tls import certificate
    _, untrusted_ca = certificate('untrusted.invalid')
    ca = tmp_path / 'untrusted.crt'
    ca.write_text(untrusted_ca)
    with pytest.raises(psycopg.OperationalError, match='certificate verify failed'):
        psycopg.connect(make_conninfo(**(params | {'sslrootcert': str(ca)})))


def test_real_connection_failure():
    with pytest.raises(psycopg.OperationalError):
        PostgresIdentityLookup('host=127.0.0.1 port=1 dbname=missing sslmode=verify-full').find('52998224725')


def test_demo_catalog_audit_accepts_real_reader_and_rejects_extra_grant(database):
    from scripts.cpf_demo import PRIVILEGES_SQL
    admin, adapter = database
    with psycopg.connect(adapter.database_url) as reader:
        assert reader.execute(PRIVILEGES_SQL).fetchone() == (True,)
    # Disposable local database fixture only; never used by the operational smoke.
    admin.execute('GRANT SELECT ON public.customers TO oficina_lambda')
    try:
        with psycopg.connect(adapter.database_url) as reader:
            assert reader.execute(PRIVILEGES_SQL).fetchone() == (False,)
    finally:
        admin.execute('REVOKE SELECT ON public.customers FROM oficina_lambda')
