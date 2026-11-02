"""Adapter regressions run even when disposable PostgreSQL is unavailable."""
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest

from auth.ports import Identity
from auth.postgres import PostgresIdentityLookup


@pytest.mark.parametrize('missing_view', [False, True])
def test_v2_query_mapping_and_no_fallback(monkeypatch, missing_view):
    connect = MagicMock()
    monkeypatch.setattr('auth.postgres.psycopg.connect', connect)
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    customer_id = UUID('00000000-0000-4000-8000-000000000001')
    cursor.fetchall.return_value = [(customer_id, True)]
    adapter = PostgresIdentityLookup('host=localhost dbname=oficina_db')
    cpf = "' OR 1=1 --"
    if missing_view:
        cursor.execute.side_effect = psycopg.errors.UndefinedTable('missing V2')
        with pytest.raises(psycopg.errors.UndefinedTable):
            adapter.find(cpf)
        cursor.fetchall.assert_not_called()
    else:
        assert adapter.find(cpf) == [Identity(customer_id, True)]
    cursor.execute.assert_called_once_with(
        'SELECT customer_id, active FROM auth.customer_identity_v2 WHERE cpf = %s LIMIT 2', (cpf,))
    connect.assert_called_once_with(
        adapter.database_url, connect_timeout=2,
        options='-c statement_timeout=2000 -c default_transaction_read_only=on')
    connect.return_value.__exit__.assert_called_once()
