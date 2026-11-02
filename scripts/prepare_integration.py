"""Apply Java V1–V11 and staging bootstrap to an EMPTY, disposable local DB."""
import argparse
import os
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict


def prepare(app, dsn):
    params = conninfo_to_dict(dsn)
    if (params.get('host') not in {'localhost', '127.0.0.1', '::1'}
            or params.get('dbname') != 'oficina_db' or params.get('hostaddr')):
        raise ValueError('Only a disposable loopback oficina_db is supported')
    migrations = app / 'src/main/resources/db/migration'
    paths = sorted(migrations.glob('V*.sql'), key=lambda p: int(p.name.split('__')[0][1:]))
    if ([int(p.name.split('__')[0][1:]) for p in paths] != list(range(1, 12))
            or paths[-1].name != 'V11__create_explicit_customer_identity_view.sql'):
        raise ValueError('Require the reviewed Java V1–V11 contract; review future migrations explicitly')
    # Read every input before changing the disposable DB; never substitute local SQL copies.
    bootstrap = (app / 'scripts/staging/01-bootstrap-roles.sql').read_text()
    reader = (app / 'scripts/bootstrap-auth-reader.sql').read_text()
    grants = (app / 'scripts/staging/02-runtime-grants.sql').read_text()
    statements = [path.read_text() for path in paths]
    with psycopg.connect(dsn, autocommit=True) as connection:
        if connection.execute("""SELECT EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('public', 'auth') AND c.relkind IN ('r', 'p', 'v', 'm'))
            OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
                ('oficina_migrator', 'oficina_api', 'oficina_lambda', 'oficina_auth_reader'))
            """).fetchone()[0]:
            raise ValueError('Require an empty disposable database and fresh cluster roles')
        connection.execute(bootstrap)
        connection.execute('SET ROLE oficina_migrator')
        try:
            for statement in statements:
                connection.execute(statement)
        finally:
            connection.execute('RESET ROLE')
        # The bootstrap admin has CREATEROLE; the migrator deliberately does not.
        connection.execute(reader)
        connection.execute(grants)
    print('Applied Java V1–V11, staging roles and runtime grants to disposable oficina_db')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('producer', type=Path)
    parser.add_argument('--disposable', required=True, action='store_true',
                        help='Confirm this local database/cluster can be discarded')
    args = parser.parse_args()
    prepare(args.producer, os.environ['TEST_DATABASE_URL'])
