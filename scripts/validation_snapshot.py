"""Scan tracked/nonignored sources and copy the working tree into a fresh directory."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def snapshot(source, destination):
    names = subprocess.check_output([
        'git', '-C', str(source), 'ls-files', '--cached', '--others', '--exclude-standard', '-z',
    ]).decode().split('\0')
    manifest = {}
    for name in sorted(set(names) - {''}):
        path = Path(name)
        original = source / path
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            raise ValueError(f'Unsafe source path: {name}')
        if original.is_symlink() or any(p.is_symlink() for p in original.parents):
            raise ValueError(f'Symlinks are not accepted in validation inputs: {name}')
        if not original.exists():  # A working-tree deletion is part of the snapshot.
            continue
        if original.is_dir() and (original / '.git').exists():
            # actions/checkout places the independent Java producer inside this checkout.
            # Its exact commit is exported separately; never copy its working tree/.git.
            continue
        data = original.read_bytes()
        if (any(p == '.aws' or p.startswith('.env') for p in path.parts)
                or path.suffix in {'.key', '.p12', '.pfx', '.tfstate', '.tfplan'}
                or re.search(rb'-----BEGIN (?:RSA |EC |ENCRYPTED |OPENSSH )?PRIVATE KEY-----[\r\n]', data)
                or re.search(rb'(?:AKIA|ASIA)[A-Z0-9]{16}', data)
                or re.search(rb'"kty"\s*:\s*"RSA"', data)
                and re.search(rb'"(?:d|p|q|dp|dq|qi)"\s*:', data)):
            raise ValueError(f'Potential secret or unsafe artifact in source: {name}')
        if path.parts[0] == 'auth' and b'customer_identity_v1' in data:
            raise ValueError(f'Forbidden V1 reference in production source: {name}')
        manifest[name] = hashlib.sha256(data).hexdigest()
    destination.mkdir(exist_ok=False)
    for name in manifest:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != manifest[name]:
            raise ValueError(f'Source changed during snapshot: {name}')
    (destination.parent / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Scanned and snapshotted {len(manifest)} files; no V1 runtime or secret pattern found')


if __name__ == '__main__':
    snapshot(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
