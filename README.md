# Oficina — Lambda CPF/JWT

Emissor Python 3.13 x86_64, handler `auth.handler.lambda_handler`, contrato HTTP API
payload 2.0. Staging permanece fechado: concorrência zero, endpoint HTTP desabilitado,
rota `AWS_IAM` e nenhuma Function URL. A janela de teste autoriza somente invocações
IAM controladas e UUIDs sintéticos explícitos. **CPF sozinho não prova identidade;
não disponibilizar emissão pública.**

O Java irmão já contém os validadores CUSTOMER RS256 e a migration V11 na branch de reconciliação.
A compatibilidade foi inspecionada; o aceite ponta a ponta não foi executado nesta
entrega. Resultados e bloqueios atuais: [relatório da sincronização V2](docs/fase3/lambda-v2-sync.md).
Contrato de implantação: [infraestrutura staging](docs/infra-staging.md).

## Contrato

`POST /auth/token`, JSON com somente `cpf`, canônico e com dígitos verificadores
válidos; limite de 1 KiB. Não aceita CPF em query string, campos extras, chaves JSON
duplicadas ou CPF formatado. A consulta parametrizada lê `auth.customer_identity_v2`.
A V11 usa `auth.customer_access.active`: ausência de registro significa desabilitado;
CPFs ambíguos por dados legados são excluídos da view. O runtime cloud também exige
UUID presente em `AUTH_SYNTHETIC_CUSTOMER_IDS`. `customers.active` é atividade de
negócio e não habilita nem revoga autenticação V2. Não há fallback para V1; não
conceder acesso à V1 como contorno. CPF formatado não é normalizado para login.

Resposta 200 contém `access_token`, `token_type=Bearer` e `expires_in=900`.
RS256/RSA >=2048 bits, `kid` autorizado, `sub` UUID canônico, `role=CUSTOMER`,
`iss`, `aud`, `iat=nbf` e `exp=iat+900`; sem CPF nos claims.
400 `INVALID_REQUEST`, 401 `AUTHENTICATION_FAILED` uniforme para ausente/inativo/
ambíguo/não autorizado, 503 `TEMPORARILY_UNAVAILABLE` sem detalhes de dependências.
Todas as respostas usam `Cache-Control: no-store`; logs não contêm CPF, token ou exceção.

## Execução e testes locais

```sh
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check auth scripts tests
python -m pytest -q -ra
```

Sem `TEST_DATABASE_URL`, os casos PostgreSQL/TLS são explicitamente ignorados.
Para executá-los, preparar um cluster PostgreSQL 16 **local descartável**, banco vazio
`oficina_db`, administrador local e TLS. `TEST_DATABASE_URL` deve conter host loopback,
`dbname=oficina_db`, `sslmode=verify-full` e `sslrootcert` (ou `PGSSLROOTCERT`).
Nunca apontar esses testes, que truncam fixtures e alteram a senha de teste, para RDS
ou banco compartilhado. A preparação recusa hosts remotos e roles/objetos existentes.

```sh
python scripts/prepare_integration.py ../tech-challenge-oficina --disposable
python -m pytest -q -ra
```

O script consome os arquivos reais Java, sem cópia de migrations: bootstrap staging 01,
V1–V11 como `oficina_migrator`, bootstrap reader como administrador e grants staging 02.
V8/V9/V10 existentes são preservadas; V11 adiciona o contrato V2. A senha descartável de `oficina_lambda` é gerada durante os testes e
retirada no teardown. Há testes de login real, grants, TLS válido/hostname errado/CA
não confiável, elegibilidade explícita e emissão restrita à lista sintética.

O contrato Java está fixado no arquivo `JAVA_CONTRACT_SHA` e na CI em
`57eb5df2c3e32fe5663596cbecfcd43a5d3ed780` (PR Java #7 integrado à main).
A CI verifica o pin e usa o mesmo comando da validação externa, sem depender de
variável remota. O script extrai o commit com `git archive`, verifica ancestralidade
em `origin/main` e nunca altera o checkout Java.

Validação integral, a partir deste repositório, em Linux com Python 3.13 (venv/pip),
Docker local acessível e Terraform 1.14.6:

```bash
bash scripts/validate_external.sh ../tech-challenge-oficina
```

O script instala as dependências em venv temporário, cria PostgreSQL 16 descartável
com porta aleatória em `127.0.0.1` e TLS `verify-full`, aplica V1–V11/grants reais,
exige todos os testes Python sem skips, Ruff, Terraform fmt/validate/11 testes mock,
dois builds idênticos, inspeção ZIP, smoke Docker Python 3.13 sem rede e diff check.
Não executa apply/destroy nem altera containers existentes. Logs, manifesto e ZIP
ficam em um diretório novo `/tmp/lambda-v2-validation.*`, informado no resumo final;
credenciais/fixtures privadas e containers desta execução são removidos no encerramento.
Qualquer falha interrompe a execução e identifica a etapa; não há aprovação parcial.
Pré-requisitos, limitações do sandbox e bloqueadores de deploy estão no
[fechamento V2](docs/fase3/lambda-v2-final.md).

Para execução manual local, configurar `APP_ENV=local`, `JWT_ISSUER`, `JWT_AUDIENCE`,
`JWT_KID`, `JWT_PRIVATE_KEY_FILE` e `DATABASE_URL`. Chaves de teste devem ser geradas
fora do checkout e apagadas depois. O runtime Lambda exige `AUTH_SECRET_ARN` e usa o
adapter Secrets Manager descrito na documentação staging; não faz fallback para arquivo.

## Build e segurança

`python scripts/build.py <sha-completo>` gera ZIP e checksum em `dist/`. Wheels
CPython 3.13 manylinux2014 x86_64 têm versões/hashes fixados em
`requirements-runtime.lock`. O build inclui somente `auth/` e dependências de runtime,
com ordem/timestamps determinísticos. `scripts/validate_zip.py` verifica layout,
bibliotecas nativas e padrões de segredos; isso não substitui revisão humana.

CI compara dois builds e executa `scripts/smoke_bundle.py` na imagem Lambda fixada por
digest, com `--network none`. Artefato de working tree/merge de CI não é release aprovada.
`contracts/jwks.public.json` contém somente chave pública de fixture, cuja privada foi
descartada; não serve como chave operacional.

O Terraform agora exige code signing `Enforce` e versões de perfis Signer aprovadas
em `signing_profile_version_arns`. O ZIP não assinado do build é insumo de release;
antes de deployment, assinar por fluxo autorizado, reinspecionar/smoke-testar o ZIP
final e usar seu checksum. Nenhuma assinatura/operação AWS foi executada localmente.

Terraform em `infra/terraform` consome contratos K3s v3 e RDS `oficina_db`, sem criar
rede, banco, endpoint ou secret values. `terraform test` usa exclusivamente mocks.
Não há CD, operações AWS, apply/destroy, branch, commit ou PR nesta entrega.

## Demonstração CPF da Fase 3

[Runbook seguro](docs/fase3/cpf-demo-runbook.md) e
[matriz de evidências esperadas](docs/fase3/cpf-demo-evidence.md).
`python -m pytest -q tests/test_cpf_demo.py` valida o smoke com AWS/HTTP/SQL mockados,
sem rede. Os modos operacionais de `scripts.cpf_demo` exigem autorização interativa
futura; invocação direta da Lambda não demonstra HTTP/IAM no Gateway desabilitado.
