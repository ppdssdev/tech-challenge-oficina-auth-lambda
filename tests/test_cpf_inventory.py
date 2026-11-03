"""Reader-only mode must never connect as auditor or read application tables."""
from unittest.mock import MagicMock

from scripts import cpf_demo, cpf_inventory


def test_counts_only_uses_one_restricted_tls_connection(capsys):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [('on',), (True,), (True,), (4, 3, 1)]
    connect = MagicMock(return_value=conn)
    evidence = cpf_demo.Evidence()
    cpf_inventory.inventory(connect, 'approved.rds.amazonaws.com', '/approved/ca.pem',
                            'private-password', None, None, evidence)
    connect.assert_called_once()
    kwargs = connect.call_args.kwargs
    assert kwargs['user'] == 'oficina_lambda'
    assert kwargs['sslmode'] == 'verify-full'
    assert kwargs['hostaddr'] == '127.0.0.1'
    assert 'default_transaction_read_only=on' in kwargs['options']
    assert conn.execute.call_count == 4
    assert conn.execute.call_args.args[0].endswith('FROM auth.customer_identity_v2')
    conn.rollback.assert_called_once()
    output = capsys.readouterr().out
    assert '"ownership": "NOT_CHECKED"' in output
    assert '"identities_v2": 4' in output
    assert 'private-password' not in output
    assert not evidence.failed
