import json
import logging
import os
import time

from auth.config import Config
from auth.errors import AuthenticationFailed, InvalidRequest
from auth.jwt import FilePrivateKeyProvider, RS256Signer
from auth.postgres import PostgresIdentityLookup
from auth.request import parse
from auth.service import AuthenticationService

logger = logging.getLogger(__name__)
_service = None
_service_deadline = 0


def build_service():
    if os.environ.get('AUTH_SECRET_ARN'):
        from auth.cloud import build_cloud_service
        return build_cloud_service()
    if os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
        raise ValueError('Cloud secret configuration required')
    config = Config.from_env()
    return AuthenticationService(
        PostgresIdentityLookup(config.database_url),
        RS256Signer(FilePrivateKeyProvider(config.key_file), config.issuer,
                    config.audience, config.kid),
    )


def handle(event, service_factory):
    try:
        cpf = parse(event)
        token = service_factory().authenticate(cpf)
        status, body = 200, dict(access_token=token, token_type='Bearer', expires_in=900)
    except InvalidRequest:
        status, body = 400, {'code': 'INVALID_REQUEST'}
    except AuthenticationFailed:
        status, body = 401, {'code': 'AUTHENTICATION_FAILED'}
    except Exception:
        # No exception message, event, identifier, CPF, token or stack trace.
        logger.error('authentication_dependency_failure')
        status, body = 503, {'code': 'TEMPORARILY_UNAVAILABLE'}
    return {'statusCode': status, 'headers': {
        'Content-Type': 'application/json', 'Cache-Control': 'no-store',
    }, 'body': json.dumps(body)}


def lambda_handler(event, context):
    def service():
        global _service, _service_deadline
        if _service is None or time.monotonic() >= _service_deadline:
            # Drop expired credentials before refresh. A failed refresh never reuses old keys.
            old = _service
            _service = None
            if old is not None and getattr(old, 'ca_file', None):
                os.unlink(old.ca_file)
            _service = build_service()
            _service_deadline = time.monotonic() + 60
        return _service
    return handle(event, service)
