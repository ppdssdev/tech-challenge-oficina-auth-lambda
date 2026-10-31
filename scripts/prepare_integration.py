"""Apply the producer's actual migrations to a disposable test database only."""
import os
import sys
from pathlib import Path
import psycopg

app = Path(sys.argv[1])
with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as connection:
    paths = sorted((app / 'src/main/resources/db/migration').glob('V*.sql'),
                   key=lambda p: int(p.name.split('__')[0][1:]))
    assert any(p.name.startswith('V10__') for p in paths)
    for path in paths:
        connection.execute(path.read_text())
    connection.execute((app / 'scripts/provision-auth-readonly.sql').read_text())
