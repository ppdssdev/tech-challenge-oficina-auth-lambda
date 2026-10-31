# Oficina — autenticação serverless

Bootstrap da Fase 3. **Não há handler de autenticação nem Lambda implantada.**
Python 3.13 proposto para o futuro handler; aplicação Java permanece no repositório principal.
Este repositório é dono da Lambda, API Gateway HTTP API, permissões de invocação,
empacotamento, contrato de login e futura stack de autenticação.

## Validação e build local

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check scripts tests
python -m unittest discover -s tests -v
python scripts/build.py "$(git rev-parse HEAD)"
```

CI `Auth contract and build`: lint, validação JSON Schema, casos positivos/negativos,
bundle determinístico `auth-contracts-<sha>.zip` e SHA-256. Não é pacote executável.
Os testes validam contratos, não autenticação, assinatura ou conexão com banco.

## Contrato v1 proposto

`POST /auth/token`, Content-Type application/json, corpo `{ "cpf": "11 dígitos" }`.
200: access_token, token_type Bearer, expires_in 900; Cache-Control: no-store.
400 INVALID_REQUEST (JSON/CPF inválido), 401 AUTHENTICATION_FAILED (ausente/inativo),
429 RATE_LIMITED e 503 TEMPORARILY_UNAVAILABLE. Nunca retornar CPF, status ou motivo
da recusa no erro. Limitar body a 1 KiB, timeout da consulta 2 s e handler 5 s.
CPF com dígitos verificadores será validado no handler; schema cobre apenas formato.
CPF sozinho não comprova identidade: atende exercício, não autenticação forte para produção.

Consulta futura: PostgreSQL privado via view versionada `auth.customer_identity_v1`
(customer_id, cpf, active), SELECT parametrizado, role somente SELECT, TLS verify-full.
View e grants via Flyway da aplicação, sem duplicar domínio Java. active=false bloqueia
novos tokens; revogação imediata de tokens existentes requer decisão adicional.
HTTP API usa integração Lambda proxy payload 2.0; permissão invoke limitada ao ARN
exato da rota/stage; throttling, sem log de corpo/Authorization. Nenhum recurso criado.

RS256, kid obrigatório, issuer/audience por ambiente, TTL 15 min; sub=UUID de cliente,
role CUSTOMER. Sem CPF no JWT. Distribuir somente chaves públicas em JWKS HTTPS;
chave privada futura no Secrets Manager com rotação e acesso mínimo da Lambda.
A integração exige migração explícita do validador HMAC atual da API.

[Arquitetura e dependências](https://github.com/ppdssdev/tech-challenge-oficina/blob/feature/fase3-multirepo-bootstrap/docs/fase3/deployment-architecture.md).
Alterações após inicialização somente por PR; staging/production ainda sem deploy.
Próxima PR: implementar handler com portas para consulta/assinatura, mocks de erros,
testes reais de assinatura/rotação e testes PostgreSQL; depois integração API Gateway.
