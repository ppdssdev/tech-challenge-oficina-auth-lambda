"""Verify layout and reject secrets before publishing the CI artifact."""
import re
import sys
import zipfile
from pathlib import PurePosixPath

with zipfile.ZipFile(sys.argv[1]) as bundle:
    names = set(bundle.namelist())
    assert {'auth/handler.py', 'jwt/__init__.py', 'psycopg/__init__.py'} <= names
    assert any(n.startswith('psycopg_binary/') and n.endswith('.so') for n in names)
    assert any(n.startswith('cryptography/') and n.endswith('.so') for n in names)
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert not any(p in {'.aws', '.git', 'tests'} or p.startswith('.env') for p in path.parts)
        assert path.suffix not in {'.pem', '.key', '.p12', '.pfx', '.pyc'}
        data = bundle.read(name)
        assert not re.search(rb'-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----[\r\n]', data)
        assert not re.search(rb'(?:AKIA|ASIA)[A-Z0-9]{16}', data)
print('ZIP layout and secret scan passed')
