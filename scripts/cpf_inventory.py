"""Read-only inventory over an operator-attested, existing private RDS tunnel."""
import argparse
import getpass
import json
import logging
import os
import re
import resource
import signal
import sys

from scripts import cpf_demo as demo


def inventory_connection(connect, host, ca, user, password):
    return connect(host=host, hostaddr='127.0.0.1', port=15432, dbname='oficina_db',
                   user=user, password=password, sslmode='verify-full', sslrootcert=ca,
                   connect_timeout=3, options='-c default_transaction_read_only=on '
                   '-c statement_timeout=3000 -c lock_timeout=1000')


def check_connection(conn):
    demo.require(conn.execute('SHOW transaction_read_only').fetchone() == ('on',), 'db_read_only')
    demo.require(conn.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
                 == (True,), 'db_tls')


def inventory(connect, host, ca, reader_password, auditor, auditor_password, evidence, data=None):
    with inventory_connection(connect, host, ca, 'oficina_lambda', reader_password) as conn:
        check_connection(conn)
        demo.require(conn.execute(demo.PRIVILEGES_SQL).fetchone() == (True,), 'db_effective_privileges')
        row = conn.execute('SELECT count(*), count(*) FILTER (WHERE active), '
                           'count(*) FILTER (WHERE NOT active) FROM auth.customer_identity_v2').fetchone()
        print(json.dumps(dict(zip(('identities_v2', 'enabled_v2', 'disabled_v2'), row))), flush=True)
        conn.rollback()
    evidence.check('reader_readonly_tls_privileges', True)
    if data is not None:
        demo.audit_database(data, reader_password, evidence, connect=connect)
    # Uses a separately supplied, already authorized auditor. Never obtains Secrets or grants.
    with inventory_connection(connect, host, ca, auditor, auditor_password) as conn:
        check_connection(conn)
        row = conn.execute('''WITH candidates AS (
            SELECT i.customer_id, i.active, count(w.id) AS orders
            FROM auth.customer_identity_v2 i
            LEFT JOIN public.work_orders w ON w.customer_id=i.customer_id
            GROUP BY i.customer_id,i.active)
            SELECT count(*) FILTER (WHERE active AND orders>0),
                   count(*) FILTER (WHERE NOT active) FROM candidates''').fetchone()
        print(json.dumps(dict(zip(('enabled_owners_with_orders', 'disabled_candidates'), row))), flush=True)
        if data is not None:
            for name in ('owner', 'other'):
                item = data[name]
                row = conn.execute('''SELECT count(*) FROM public.work_orders w
                    JOIN public.vehicles v ON v.id=w.vehicle_id AND v.customer_id=w.customer_id
                    WHERE w.id=%s AND w.customer_id=%s''',
                    (item['order_id'], item['customer_id'])).fetchone()
                evidence.check('ownership_' + name, row == (1,))
        conn.rollback()
    evidence.check('auditor_readonly_tls', True)
    evidence.check('selected_fixture_checks_complete', data is not None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--ca', required=True)
    parser.add_argument('--input')
    parser.add_argument('--authorized', action='store_true')
    args = parser.parse_args(argv)
    evidence = demo.Evidence()
    try:
        demo.require(args.authorized and sys.stdin.isatty() and not os.environ.get('CI'), 'operator_gate')
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        logging.disable(logging.CRITICAL)
        signal.signal(signal.SIGALRM, demo.interrupted)
        signal.alarm(300)
        demo.require(getpass.getpass('Digite ATESTO TUNEL SSM PRIVADO: ')
                     == 'ATESTO TUNEL SSM PRIVADO', 'ssm_tunnel_attestation')
        clients = demo.clients()
        lam = clients['lambda']
        demo.require(lam.get_function_concurrency(FunctionName=demo.FUNCTION).get(
            'ReservedConcurrentExecutions') == 0, 'initial_concurrency_zero')
        config = lam.get_function_configuration(FunctionName=demo.FUNCTION)
        env = config['Environment']['Variables']
        demo.require(env['DB_HOST'] == args.host and env['DB_NAME'] == 'oficina_db'
                     and re.fullmatch(r'[A-Za-z0-9.-]+\.us-east-2\.rds\.amazonaws\.com', args.host),
                     'rds_hostname_contract')
        allowed = json.loads(env['AUTH_SYNTHETIC_CUSTOMER_IDS'])
        print(json.dumps({'allowlist_count': len(allowed), 'allowlist_distinct': len(set(allowed))}))
        data = demo.private_json(args.input) if args.input else None
        if data is not None:
            demo.validate_inputs(data)
            demo.require(data['db_host'] == args.host and data['db_ca_file'] == args.ca
                         and data.get('db_transport') == 'ssm-loopback'
                         and isinstance(data.get('provenance_ref'), str) and data['provenance_ref'].strip(),
                         'private_inventory_provenance_and_transport')
            for name in ('owner', 'other', 'inactive'):
                evidence.check('allowlist_' + name, data[name]['customer_id'] in allowed)
        reader_password = getpass.getpass('Senha do reader oficina_lambda (sem eco): ')
        auditor = input('Papel auditor já autorizado (não usar administrador automaticamente): ').strip()
        demo.require(re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', auditor), 'auditor_role_shape')
        auditor_password = getpass.getpass('Senha do auditor (sem eco): ')
        import psycopg
        inventory(psycopg.connect, args.host, args.ca, reader_password, auditor, auditor_password,
                  evidence, data)
        del reader_password, auditor_password
        evidence.check('final_concurrency_zero', lam.get_function_concurrency(
            FunctionName=demo.FUNCTION).get('ReservedConcurrentExecutions') == 0)
        return int(evidence.failed)
    except demo.CheckFailed as failure:
        evidence.check(str(failure), False)
        return 1
    except BaseException:
        evidence.check('readonly_inventory_incomplete', False)
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
