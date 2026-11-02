# Fase 3 — sincronização Lambda com SQL V2

Revisão local em 2026-11-02. Implementação preparada; **deploy não liberado**.
As alterações preexistentes da Lambda foram preservadas. Nenhuma operação AWS,
terraform apply, commit, push ou abertura de endpoint foi realizada. Arquivos Java
permaneceram idênticos ao início desta sessão, conferidos por snapshot SHA-256.

## Contrato conferido no Java

Fontes: `src/main/resources/db/migration/V11__create_explicit_customer_identity_view.sql`,
`docs/fase3/reconciliation.md`, migrations anteriores, bootstrap reader e scripts
staging 01/02 do repositório irmão. V1–V10 não são substituídas por SQL local.

| Coluna V2 | Tipo SQL | Semântica / consumo Python |
| --- | --- | --- |
| `customer_id` | `uuid` | `customers.id`, convertido por psycopg em `UUID`; `Identity.customer_id` e claim `sub` |
| `cpf` | `varchar(14)` herdado de `document_number` | Somente valores com exatamente 11 dígitos ASCII são publicados; filtro parametrizado `%s` |
| `active` | `boolean` | `coalesce(customer_access.active, false)`; ausência de habilitação significa false; `Identity.active` deve ser exatamente `True` |

`auth.customer_access` tem PK/FK UUID com ON DELETE CASCADE e `active NOT NULL DEFAULT
false`. A view usa `security_barrier=true`. Apenas `document_type='CPF'` e documento
canônico entram na V2. CNPJ, documentos formatados e demais valores não canônicos
não se tornam identidades de login. Os dígitos verificadores são responsabilidade
da API/Lambda; a view não os calcula.

`regexp_replace(document_number, '[^0-9]', '', 'g')` é usado somente para detectar
colisões com **qualquer outra linha CPF**, inclusive legada, inativa ou sem
habilitação. Havendo colisão, a identidade canônica é omitida. O índice de expressão
é não único; a migration não corrige nem escolhe dados legados. A Lambda não
normaliza entrada formatada para contornar essa regra.

`customers.active` é atividade de negócio, independente da habilitação de login:
false no negócio e true em `customer_access` ainda permitem emissão V2. Para
suspender novas emissões, inativar/remover a habilitação explícita. Tokens emitidos
continuam válidos até expiração; não foi introduzida revogação por requisição.

## Implementação e segurança

A query agora lê exclusivamente `auth.customer_identity_v2`, mantendo `WHERE cpf =
%s LIMIT 2`, timeout de conexão/statement de 2 s, conexão por invocação e transação
de leitura. `Identity(UUID, bool)` continua compatível, sem coerção de tipos. O
serviço preserva exatamente uma linha e `active is True`; ausente/inativo/ambíguo
resultam em 401 uniforme. Falta da V2 é falha de dependência, sem fallback V1.
`SyntheticIdentityLookup` mantém a allowlist obrigatória de UUIDs sintéticos.

Privilégios mínimos revisados: CONNECT em `oficina_db` para o LOGIN
`oficina_lambda`; membership somente em `oficina_auth_reader` NOLOGIN; USAGE em
`auth` e SELECT somente em V2. Sem V1, `auth_readonly`, tabelas-base, escrita,
sequências, DDL/TEMP, papel migrator/API ou atributos administrativos. Os scripts
estritos do Java já expressam esses grants; não foi necessário alterá-los ou
adicionar grants na Lambda. A auditoria SQL exclui somente V2 e inclui privilégios
efetivos PUBLIC/herdados/por coluna; há teste explícito de negação da V1.

Preparação descartável aceita exatamente V1–V11, com o nome revisado da V11, e
rejeita migrations ausentes/futuras antes de conectar. Continua limitada a loopback,
`oficina_db` vazio e roles novas, lendo SQL real Java: bootstrap 01 como admin,
migrations como migrator, reader e grants 02 como admin.

CI exige SHA completo, checkout exato, ancestralidade na main Java e presença de
V11. PostgreSQL/TLS deve executar sem skips. A inspeção do ZIP exige V2 e rejeita
referências V1 no runtime; o smoke verifica a query do adapter empacotado com
conexão mockada, além de assinatura RS256 e claims. Esse smoke não substitui SQL/TLS.
IAM, allowlist, assinatura de código `Enforce`, endpoint desabilitado e concorrência
fechada permanecem preservados; nenhum arquivo Terraform de configuração foi alterado.

## JWT e compatibilidade Java

Emissor `auth/jwt.py` preservado: RSA >=2048, RS256, `kid`, `iss`, `aud`, `sub` UUID
canônico, `role=CUSTOMER`, `iat=nbf`, `exp=iat+900`, sem CPF/roles/authorities.
Testes Python verificam assinatura, chave/algoritmo/issuer/audience incorretos,
expiração, ativação futura, chave fraca e claims exatos.

A inspeção de `CustomerJwtVerifier.java` (PEM por código) e
`CustomerSecurityConfig.java` (JWKS em `/api/v1/customer/**`) confirma compatibilidade
desse formato. Configurar a mesma chave pública/kid/issuer/audience nas duas cadeias:
`app.jwt.customer` para PEM e `CUSTOMER_JWKS_URI`, `CUSTOMER_JWT_ISSUER`,
`CUSTOMER_JWT_AUDIENCE` para JWKS. PEM usa skew 30 s; JWKS admite até 60 s e cache até
300 s. HMAC administrativo permanece independente. Validação Java ponta a ponta,
ownership e rotação com a configuração operacional continuam pendentes.

## Resultados locais

| Validação | Resultado |
| --- | --- |
| Pytest completo, Python 3.12.3 disponível | **76 passed, 21 skipped**, zero falhas; `dist/tests-fase3-v2.xml` |
| Novas regressões unitárias | Query V2 parametrizada/mapeamento/sem fallback; rejeição de contrato ausente, incompleto, futuro ou V11 inesperada |
| PostgreSQL/TLS | 21 casos preparados, não executados: Docker recusou acesso a `/var/run/docker.sock`; sem TEST_DATABASE_URL utilizável |
| Ruff e `git diff --check` | Aprovados |
| Terraform fmt | Aprovado |
| Terraform validate/test | Tentados e bloqueados na inicialização do provider AWS 6.38.0: `listen unix ... setsockopt: operation not permitted`; nenhum teste mock executou |
| Políticas Terraform estruturais em Python | 6 testes aprovados, incluídos nos 76; IAM, endpoint, concorrência e code signing preservados |
| Build CPython 3.13 x86_64 | Dois builds offline, wheels locais com hashes fixados, ZIPs idênticos; fontes `auth/` empacotadas iguais ao checkout |
| Inspeção do artefato | Layout, bibliotecas nativas, contrato V2 e scan de segredos aprovados; cópia temporária adulterada para V1 foi rejeitada |
| Smoke Python 3.13 oficial | Tentado com `--network none --pull=never`; bloqueado pela permissão Docker. Python 3.13 nativo não disponível; não substituído por 3.12 |
| Scan de arquivos/segredos | detect-secrets offline, incluindo não rastreados; achados revisados como hashes/digests, JWKS público, referências e fixtures sintéticas; nenhum segredo operacional identificado |
| Preservação Java | Snapshot SHA-256 dos arquivos antes/depois idêntico; sem escrita no irmão |

Os testes SQL incluem tipos, ausência/default de habilitação, independência da
atividade de negócio, CPF legado/canônico/ambíguo, exclusão, login real, grants,
TLS/hostname/CA e handler com emissão restrita. Sem executar esses testes, não há
aceite do contrato SQL efetivo. A CI mantém a reprovação por qualquer skip.

Artefato local não assinado, derivado do **working tree**, não de uma release limpa:
`dist/auth-lambda-python313-x86_64-bbbf36eecd57a4f35e1792702a13264a7d74ea75.zip`.
O SHA no nome é o HEAD Lambda usado pelo script, não comprova que as mudanças estão
commitadas. SHA-256 dos bytes:
`002c93e1108b52a1640404b49b1c1529522720f739c94fe20b625b569cf37940`.
Esse ZIP não está autorizado para deployment sob code signing Enforce.

## JAVA_CONTRACT_SHA e pendências

Atualização após PR Java #7: o pin definitivo é
`57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`, conferido em HEAD/main/origin/main do Java.
O arquivo `JAVA_CONTRACT_SHA` e a CI usam esse commit, sem variável remota pendente.
A validação integral agora é `bash scripts/validate_external.sh ../tech-challenge-oficina`.
Os resultados locais acima são históricos; consulte o
[fechamento definitivo V2](lambda-v2-final.md) para a evidência mais recente e os
bloqueadores restantes.

Antes de qualquer aceite staging: executar a suíte sem skips em PostgreSQL 16/TLS,
Terraform validate/test em runner permitido e smoke do ZIP em Python 3.13; validar
JWT real com as duas cadeias Java e ownership; preparar release assinada por fluxo
autorizado e reinspecionar o ZIP final. Manter somente CPFs/UUIDs sintéticos e acesso
IAM controlado. Nenhuma configuração remota ou execução AWS faz parte desta entrega.
Comandos para PostgreSQL/TLS e smoke estão em
[Prompt 13](../prompt13-security-integration.md#comandos-fora-do-sandbox), usando agora
Java V1–V11. O contrato operacional está em [staging](../infra-staging.md).

## Arquivos alterados nesta sincronização

- `auth/postgres.py`: consumo exclusivo V2.
- `scripts/prepare_integration.py`: contrato revisado V1–V11.
- `tests/test_prepare_integration.py`: rejeição de contratos incompatíveis.
- `tests/test_postgres.py`: fixtures e integração V2, tipos, semântica e grants.
- `tests/test_postgres_adapter.py` (novo): regressões do adapter sem Docker.
- `scripts/smoke_bundle.py`, `scripts/validate_zip.py`: V2 no artefato e claims.
- `.github/workflows/ci.yml`: pin Java integrado à main e V11 obrigatório.
- `README.md`, `docs/infra-staging.md`: instruções operacionais V2/JWKS/PEM.
- `docs/prompt04-delivery.md`, `docs/prompt12-integration.md`,
  `docs/prompt13-security-integration.md`: identificação de evidência histórica e
  procedimento atual do pin, evitando usar os relatórios antigos como aceite V2.
- `docs/fase3/lambda-v2-sync.md` (novo): este relatório.

Artefatos ignorados gerados: ZIP/checksum/XML em `dist/` e fixture ZIP dos testes
Terraform. Logs/snapshots desta sessão ficam em `/tmp/fase3-lambda-*`. O `git status`
inclui outras alterações anteriores a esta tarefa; elas não são atribuídas à V2.
