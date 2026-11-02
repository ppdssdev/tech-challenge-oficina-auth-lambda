"""Generate ephemeral localhost PostgreSQL TLS fixtures OUTSIDE release artifacts."""
import argparse
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def certificate(common_name='localhost'):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName(common_name), x509.IPAddress(ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    directory = parser.parse_args().directory.resolve()
    root = Path(__file__).resolve().parents[1]
    if directory == root or root in directory.parents:
        raise SystemExit('TLS private fixture must be generated outside the checkout')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    key, cert = certificate()
    with (directory / 'server.key').open('xb') as target:
        (directory / 'server.key').chmod(0o600)
        target.write(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (directory / 'server.crt').write_text(cert)
