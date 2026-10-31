import base64
import binascii
import json
import re

from auth.errors import InvalidRequest


def valid_cpf(cpf):
    if not isinstance(cpf, str) or not re.fullmatch(r"[0-9]{11}", cpf):
        return False
    if len(set(cpf)) == 1:
        return False
    digits = list(map(int, cpf))
    for size in (9, 10):
        digit = (sum(n * (size + 1 - i) for i, n in enumerate(digits[:size])) * 10) % 11
        if digits[size] != (0 if digit == 10 else digit):
            return False
    return True


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest()
        result[key] = value
    return result


def parse(event):
    try:
        if event['version'] != '2.0':
            raise InvalidRequest()
        http = event['requestContext']['http']
        if http['method'] != 'POST' or event['rawPath'] != '/auth/token':
            raise InvalidRequest()
        if http['path'] != '/auth/token':
            raise InvalidRequest()
        if event.get('rawQueryString', '') or event.get('queryStringParameters'):
            raise InvalidRequest()
        headers = {k.lower(): v for k, v in event['headers'].items()}
        if headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise InvalidRequest()
        body = event['body']
        encoded = event['isBase64Encoded']
        if not isinstance(body, str) or type(encoded) is not bool:
            raise InvalidRequest()
        if len(body) > (1368 if encoded else 1024):
            raise InvalidRequest()
        raw = base64.b64decode(body, validate=True) if encoded else body.encode('utf-8')
        if len(raw) > 1024:
            raise InvalidRequest()
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {'cpf'} or not valid_cpf(data['cpf']):
            raise InvalidRequest()
        return data['cpf']
    except (KeyError, TypeError, ValueError, AttributeError, binascii.Error) as exc:
        raise InvalidRequest() from exc
