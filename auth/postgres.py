import psycopg

from auth.ports import Identity


class PostgresIdentityLookup:
    def __init__(self, database_url):
        self.database_url = database_url

    def find(self, cpf):
        # One connection per invocation, closed on success and exceptions. No pool.
        with psycopg.connect(
            self.database_url, connect_timeout=2,
            options='-c statement_timeout=2000 -c default_transaction_read_only=on',
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT customer_id, active FROM auth.customer_identity_v1 '
                    'WHERE cpf = %s LIMIT 2', (cpf,),
                )
                return [Identity(*row) for row in cursor.fetchall()]
