"""Deterministic executable ZIP for Lambda Python 3.13 / x86_64."""
import hashlib
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = sys.argv[1]
if not re.fullmatch(r'[0-9a-f]{40}', version):
    raise SystemExit('Expected full Git commit SHA')
out = root / 'dist'
out.mkdir(exist_ok=True)
artifact = out / f'auth-lambda-python313-x86_64-{version}.zip'
with tempfile.TemporaryDirectory() as temporary:
    target = Path(temporary)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--require-hashes',
                    '--platform', 'manylinux2014_x86_64', '--python-version', '3.13',
                    '--implementation', 'cp', '--only-binary=:all:', '--no-compile',
                    '--target', str(target), '-r', str(root / 'requirements-runtime.lock')],
                   check=True)
    import shutil
    shutil.copytree(root / 'auth', target / 'auth', ignore=shutil.ignore_patterns('__pycache__'))
    with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(target.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            entry = zipfile.ZipInfo(path.relative_to(target).as_posix(), (1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry, path.read_bytes())
(out / f'{artifact.name}.sha256').write_text(
    f'{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n')
subprocess.run([sys.executable, str(root / 'scripts/validate_zip.py'), str(artifact)], check=True)
print(artifact.name)
