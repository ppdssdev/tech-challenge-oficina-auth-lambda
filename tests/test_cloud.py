import json
import os
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from psycopg.conninfo import conninfo_to_dict

from auth.cloud import SyntheticIdentityLookup, build_cloud_service
from auth.ports import Identity
from scripts.prepare_test_tls import certificate


@pytest.fixture
def configuration(monkeypatch):
    subject = str(uuid4())
    for name, value in dict(APP_ENV='staging', AUTH_SYNTHETIC_CUSTOMER_IDS=json.dumps([subject]),
        DB_HOST='synthetic.us-east-2.rds.amazonaws.com', DB_PORT='5432', DB_NAME='oficina_db',
        AUTH_SECRET_ARN='synthetic-secret-reference', JWT_ISSUER='https://issuer.test',
        JWT_AUDIENCE='synthetic-api', JWT_KID='test').items():
        monkeypatch.setenv(name, value)
    key, cert = certificate()
    payload = dict(username='oficina_lambda', password=str(uuid4()), kid='test',
        private_key_pem=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode(),
        ca_pem=cert)
    return subject, payload


def test_secret_is_consumed_only_in_memory_and_tls_cannot_be_overridden(configuration):
    subject, payload = configuration
    client = Mock(get_secret_value=Mock(return_value={'SecretString': json.dumps(payload)}))
    service = build_cloud_service(client)
    try:
        params = conninfo_to_dict(service.identities.delegate.database_url)
        assert params['sslmode'] == 'verify-full'
        assert params['user'] == 'oficina_lambda'
        assert params['dbname'] == 'oficina_db'
        assert Path(params['sslrootcert']).read_text() == payload['ca_pem']
        assert os.stat(params['sslrootcert']).st_mode & 0o777 == 0o600
        assert service.signer.sign(subject)
        client.get_secret_value.assert_called_once_with(SecretId='synthetic-secret-reference', VersionStage='AWSCURRENT')
    finally:
        os.unlink(service.ca_file)


@pytest.mark.parametrize('field,value', [('username', 'oficina_admin'), ('kid', 'other'),
    ('password', ''), ('ca_pem', 'invalid'), ('private_key_pem', 'invalid')])
def test_bad_secret_fails_closed(configuration, field, value):
    _, payload = configuration
    payload[field] = value
    with pytest.raises(Exception):
        build_cloud_service(Mock(get_secret_value=Mock(return_value={'SecretString': json.dumps(payload)})))


def test_cloud_scope_and_missing_allowlist_fail_before_secret_fetch(configuration, monkeypatch):
    client = Mock()
    for name, value in [('APP_ENV', 'production'), ('AUTH_SYNTHETIC_CUSTOMER_IDS', '[]'),
                        ('DB_NAME', 'oficina'), ('DB_HOST', '127.0.0.1')]:
        with monkeypatch.context() as patch:
            patch.setenv(name, value)
            with pytest.raises(ValueError):
                build_cloud_service(client)
    client.get_secret_value.assert_not_called()


def test_only_explicit_synthetic_uuid_is_eligible():
    allowed = str(uuid4())
    delegate = Mock()
    lookup = SyntheticIdentityLookup(delegate, {allowed})
    for rows in [[], [Identity(uuid4(), True)], [Identity(allowed, True), Identity(allowed, True)]]:
        delegate.find.return_value = rows
        assert lookup.find('synthetic') == []
    delegate.find.return_value = [Identity(allowed, True)]
    assert lookup.find('synthetic') == delegate.find.return_value


def test_expired_cache_never_falls_back_to_old_secret(monkeypatch, caplog):
    import auth.handler as handler
    from test_runtime import event
    old = Mock()
    old.ca_file = None
    monkeypatch.setattr(handler, '_service', old)
    monkeypatch.setattr(handler, '_service_deadline', 0)
    monkeypatch.setattr(handler, 'build_service', Mock(side_effect=RuntimeError('secret-password-private-key')))
    assert handler.lambda_handler(event(), None)['statusCode'] == 503
    old.authenticate.assert_not_called()
    assert 'secret-password-private-key' not in caplog.text
    assert handler._service is None
