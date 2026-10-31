import json
import logging

from auth.config import Config
from auth.errors import AuthenticationFailed, InvalidRequest
from auth.jwt import FilePrivateKeyProvider, RS256Signer
from auth.postgres import PostgresIdentityLookup
from auth.request import parse
from auth.service import AuthenticationService

logger = logging.getLogger(__name__)
_service = None


def build_service():
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
        global _service
        if _service is None:
            _service = build_service()
        return _service
    return handle(event, service)
