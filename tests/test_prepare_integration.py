"""Destructive test setup must reject non-local or unreviewed producer inputs first."""
from unittest.mock import Mock

import pytest

from scripts.prepare_integration import prepare


@pytest.mark.parametrize('dsn', [
    'host=database.us-east-2.rds.amazonaws.com dbname=oficina_db',
    'host=localhost dbname=postgres',
    'host=localhost hostaddr=192.0.2.1 dbname=oficina_db',
    'dbname=oficina_db',
])
def test_prepare_rejects_unsafe_target_before_connect(monkeypatch, tmp_path, dsn):
    connect = Mock()
    monkeypatch.setattr('scripts.prepare_integration.psycopg.connect', connect)
    with pytest.raises(ValueError, match='disposable loopback'):
        prepare(tmp_path, dsn)
    connect.assert_not_called()


def test_prepare_rejects_missing_java_contract_before_connect(monkeypatch, tmp_path):
    connect = Mock()
    monkeypatch.setattr('scripts.prepare_integration.psycopg.connect', connect)
    with pytest.raises(ValueError, match='Java V1–V11'):
        prepare(tmp_path, 'host=127.0.0.1 dbname=oficina_db')
    connect.assert_not_called()


@pytest.mark.parametrize('versions,last_name', [
    (range(1, 11), 'V10__customer_identity_view.sql'),
    (range(1, 13), 'V12__future.sql'),
    ([1, 2, 3, 4, 5, 6, 7, 8, 10, 11], 'V11__create_explicit_customer_identity_view.sql'),
    (range(1, 12), 'V11__unexpected.sql'),
])
def test_prepare_rejects_incomplete_or_future_contract(monkeypatch, tmp_path, versions, last_name):
    migrations = tmp_path / 'src/main/resources/db/migration'
    migrations.mkdir(parents=True)
    for version in versions:
        name = last_name if version == max(versions) else f'V{version}__fixture.sql'
        (migrations / name).write_text('-- rejection fixture only')
    connect = Mock()
    monkeypatch.setattr('scripts.prepare_integration.psycopg.connect', connect)
    with pytest.raises(ValueError, match='Java V1–V11'):
        prepare(tmp_path, 'host=127.0.0.1 dbname=oficina_db')
    connect.assert_not_called()
