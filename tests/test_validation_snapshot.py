"""The external validator must not copy credentials or follow paths outside the checkout."""
import json
from unittest.mock import Mock

import pytest

from scripts.validation_snapshot import snapshot


def sources(monkeypatch, tmp_path, files):
    source = tmp_path / 'source'
    source.mkdir()
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr('scripts.validation_snapshot.subprocess.check_output',
                        Mock(return_value='\0'.join(files).encode() + b'\0'))
    return source


@pytest.mark.parametrize('name,content', [
    ('.env.production', b'fixture'),
    ('.aws/credentials', b'fixture'),
    ('credentials.key', b'fixture'),
    ('public.txt', b'-----BEGIN PRIVATE KEY-----\nsynthetic'),
    ('public.txt', b'-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic'),
    ('public.txt', b'AKIA' + b'0' * 16),
    ('key.json', json.dumps({'kty': 'RSA', 'n': 'public', 'd': 'private'}).encode()),
    ('auth/adapter.py', b'SELECT * FROM auth.customer_identity_v1'),
])
def test_rejects_sensitive_or_legacy_runtime_input_before_copy(monkeypatch, tmp_path, name, content):
    source = sources(monkeypatch, tmp_path, {name: content})
    destination = tmp_path / 'snapshot'
    with pytest.raises(ValueError):
        snapshot(source, destination)
    assert not destination.exists()
    assert (source / name).read_bytes() == content


def test_rejects_symlink_to_external_file(monkeypatch, tmp_path):
    source = sources(monkeypatch, tmp_path, {'link': b'fixture'})
    (source / 'link').unlink()
    (source / 'link').symlink_to(tmp_path / 'external')
    with pytest.raises(ValueError, match='Symlinks'):
        snapshot(source, tmp_path / 'snapshot')


def test_copies_current_sources_without_nested_java_or_overwriting_destination(monkeypatch, tmp_path):
    source = sources(monkeypatch, tmp_path, {'auth/adapter.py': b'customer_identity_v2'})
    (source / 'producer/.git').mkdir(parents=True)
    monkeypatch.setattr('scripts.validation_snapshot.subprocess.check_output',
                        Mock(return_value=b'auth/adapter.py\0producer/\0deleted.py\0'))
    destination = tmp_path / 'snapshot'
    snapshot(source, destination)
    assert (destination / 'auth/adapter.py').read_bytes() == b'customer_identity_v2'
    assert not (destination / 'producer').exists()
    manifest = json.loads((tmp_path / 'source-manifest.json').read_text())
    assert set(manifest) == {'auth/adapter.py'}
    with pytest.raises(FileExistsError):
        snapshot(source, destination)
    assert (destination / 'auth/adapter.py').read_bytes() == b'customer_identity_v2'
