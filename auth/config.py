import os
from dataclasses import dataclass
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict


@dataclass(frozen=True, repr=False)
class Config:
    issuer: str
    audience: str
    kid: str
    key_file: str
    database_url: str
    local: bool

    @classmethod
    def from_env(cls):
        names = ('JWT_ISSUER', 'JWT_AUDIENCE', 'JWT_KID', 'JWT_PRIVATE_KEY_FILE', 'DATABASE_URL')
        values = [os.environ.get(name, '').strip() for name in names]
        if not all(values):
            raise ValueError('Missing configuration')
        environment = os.environ.get('APP_ENV')
        if environment not in ('local', 'staging', 'production'):
            raise ValueError('Invalid environment')
        local = environment == 'local' and not os.environ.get('AWS_LAMBDA_FUNCTION_NAME')
        config = cls(*values, local=local)
        params = conninfo_to_dict(config.database_url)
        # Single endpoint bounds connection timeout; never accept multi-host fallback.
        if not params.get('host') or ',' in params['host'] or 'hostaddr' in params:
            raise ValueError('Single database hostname required')
        if not local:
            if (params.get('sslmode') != 'verify-full'
                    or not Path(params.get('sslrootcert', '')).is_file()
                    or not config.issuer.startswith('https://')):
                raise ValueError('Verified TLS required')
        return config
