"""Create a private owner-only input from existing records; never opens Lambda."""
import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import resource
import stat
import sys

from scripts import cpf_demo as demo


def read_private_text(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        demo.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                     and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= demo.LIMIT,
                     'private_input_permissions')
        return source.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        logging.disable(logging.CRITICAL)
        demo.require(sys.stdin.isatty() and not os.environ.get('CI'), 'operator_gate')
        demo.require(getpass.getpass('Digite CONFIRMO A SINTETICA OS PROPRIA ADMIN PRONTO E TUNEIS: ')
                     == 'CONFIRMO A SINTETICA OS PROPRIA ADMIN PRONTO E TUNEIS', 'owner_attestation')
        root = Path.home() / '.config/oficina/staging'
        fixture = dict(line.split('=', 1) for line in read_private_text(
            root / 'lambda-fixture.env').splitlines() if '=' in line)
        orders = demo.private_json(root / 'os-demo-results.json')
        demo.require(orders['customerId'] == fixture['FIXTURE_UUID'] and orders['orders'],
                     'fixture_order_record')
        evidence = json.loads(Path('docs/fase3/java-owner-window-trust-evidence.json').read_text())
        c = demo.clients()
        demo.require(c['lambda'].get_function_concurrency(FunctionName=demo.FUNCTION).get(
            'ReservedConcurrentExecutions') == 0, 'initial_concurrency_zero')
        config = c['lambda'].get_function_configuration(FunctionName=demo.FUNCTION)
        env = config['Environment']['Variables']
        data = dict(synthetic_only=True, owner_order_confirmed=True, admin_trust_ready=True,
                    provenance_ref=str(root / 'lambda-fixture.env'),
                    version='$LATEST', code_sha256=config['CodeSha256'],
                    java_url=demo.JAVA_SSM_ORIGIN, java_transport='ssm-loopback',
                    ssm_tunnel_attested=True, db_transport='ssm-loopback',
                    db_host=env['DB_HOST'], db_ca_file=str(root / 'rds-ca.pem'),
                    jwks_url=evidence['jwks_uri'], issuer=env['JWT_ISSUER'],
                    audience=env['JWT_AUDIENCE'], kid=env['JWT_KID'],
                    owner=dict(cpf=fixture['FIXTURE_CPF'], customer_id=fixture['FIXTURE_UUID'],
                               order_id=orders['orders'][0]['id']))
        demo.validate_owner_inputs(data)
        demo.preflight(c, data, demo.Evidence(), identity_names=('owner',))
        destination = Path(args.output)
        parent = destination.parent.stat()
        demo.require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
                     'private_directory_permissions')
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
        print('{"check":"owner_input_created","result":"PASS"}')
        return 0
    except BaseException:
        print('{"check":"owner_input_preparation","result":"FAIL"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
