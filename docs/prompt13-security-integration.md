> **Registro histórico:** resultados e pendências abaixo pertencem à etapa original.
> Estado vigente em 2026-11-03: [consolidação auditada](fase3-evidence.md).

# Prompt 13 — segurança e integração antes do deploy

> Evidência histórica, anterior à reconciliação V11. Referências a V8/V1, hashes,
> contagens e resultados abaixo não autorizam staging. O contrato operacional atual
> exige V1–V11, leitura exclusiva de V2 e pin Java integrado no PR #7; consulte
> [o fechamento V2](fase3/lambda-v2-final.md) e [staging](infra-staging.md).

**Parecer: NÃO APTO para revisão de terraform plan com intenção de implantação.**
As mudanças de código podem ser revisadas agora, mas faltam evidências de PostgreSQL,
Terraform/provider e runtime Python 3.13, além dos insumos de release. Não foi feito
plan real, nenhuma operação AWS, alteração de credencial operacional ou de state.

## Tratamento individual das oito falhas

A classificação avalia este staging fechado, sem dados pessoais nos logs/env,
invocação síncrona e retenção explicitamente limitada a 7 dias. Um achado de ferramenta
não comprova, por si só, exploração ou exposição em AWS ainda não provisionada.
Nenhuma supressão Checkov, skip-check ou soft-fail foi acrescentado.

| # | Regra / recurso | Classificação | Tratamento e estado final |
| --- | --- | --- | --- |
| 1 | CKV_AWS_338 / log group `lambda` | Regra incompatível com decisão arquitetural | Mantidos 7 dias conforme limite do projeto; aumentar para 365 dias contrariaria a retenção solicitada. Permanece falha visível. Reavaliar se surgir obrigação de auditoria anual. |
| 2 | CKV_AWS_158 / log group `lambda` | Requisito válido de hardening, não implementado | CMK própria permite controle/revogação segregados, mas não é equivalente a habilitar criptografia: CloudWatch já criptografa logs em repouso por padrão. Mantida criptografia gerenciada do serviço, sem novos recursos KMS/custos/policy. Pendência de decisão de hardening, não evidência de log em claro. |
| 3 | CKV_AWS_116 / função `auth` | Regra incompatível com o contrato síncrono | Sem fonte de eventos assíncrona declarada; cliente recebe resposta HTTP/JWT. DLQ atende eventos assíncronos e armazenaria payload que pode conter CPF. Não foi criada fila/permissão SQS/SNS. Permanece falha visível. |
| 4 | CKV_AWS_272 / função `auth` | Requisito válido não implementado; lacuna de integridade do deployment | **Corrigido no Terraform**: code signing associado à função, política `Enforce`, versões imutáveis de perfis da conta/região autorizadas. Sem fallback Warn e sem permissão Signer no runtime. Checkov agora aprova esta regra; a assinatura real ainda precisa ser produzida em fluxo autorizado. |
| 5 | CKV_AWS_50 / função `auth` | Regra incompatível com a observabilidade mínima documentada | Mantidos logs sanitizados/métricas existentes, sem tracing ativo ou permissões X-Ray. X-Ray é instrumentação adicional, não controle de autenticação/criptografia. Sem requisito atual de rastreamento distribuído; reavaliar antes de ampliar o serviço. Falha visível. |
| 6 | CKV_AWS_173 / função `auth` | Requisito válido de hardening, não implementado | CMK própria para environment não foi adicionada. Environment tem referências/configuração, sem senha/chave privada; Lambda criptografa em repouso por padrão. Secret values permanecem no Secrets Manager e em memória. Revisar CMK se houver requisito de controle de chave; não é exposição de segredo em claro. |
| 7 | CKV_AWS_338 / log group `gateway` | Regra incompatível com decisão arquitetural | Mantidos 7 dias e somente requestId/status/routeKey. Não aumentar retenção para satisfazer o scanner. Falha visível. |
| 8 | CKV_AWS_158 / log group `gateway` | Requisito válido de hardening, não implementado | Mesma análise do item 2, aplicada separadamente ao log do gateway. Criptografia gerenciada mantida; CMK própria continua ausente e o achado permanece visível. |

As justificativas são propostas específicas para a revisão, **não exceções aprovadas
por um responsável externo**. Os itens 2/6/8 continuam requisitos de hardening não
implementados; caso a política do projeto exija CMK, precisam de implementação e
validação da key policy antes de plan. Não há conclusão de “segurança aprovada”.

No item 3, um operador com `lambda:InvokeFunction` ainda pode escolher `InvocationType=Event`;
AWS_IAM não prova bloqueio técnico de toda invocação assíncrona. O procedimento suportado
é `RequestResponse`, sem automações assíncronas. Se esse escopo mudar, revisar retenção,
retentativas, destinatários e dados pessoais antes de aceitar a exceção de DLQ.

Fontes oficiais que sustentam a análise:
[CloudWatch Logs criptografa todos os log groups](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/data-protection.html),
[criptografia em repouso Lambda](https://docs.aws.amazon.com/lambda/latest/dg/security-encryption-at-rest.html),
[DLQ e registros assíncronos](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-retain-records.html),
[tracing X-Ray e permissões](https://docs.aws.amazon.com/lambda/latest/dg/services-xray.html),
[code signing e Enforce](https://docs.aws.amazon.com/lambda/latest/dg/configuration-codesigning.html).

## Correção de assinatura e limites

Novo recurso `aws_lambda_code_signing_config.auth`, ligado a
`aws_lambda_function.auth.code_signing_config_arn`. A variável obrigatória
`signing_profile_version_arns` aceita de 1 a 20 ARNs de versões, sem wildcard, apenas
na conta informada e em us-east-2. Não há valor default, ARN operacional inventado
ou switch para remover enforcement. Perfis e assinatura não são criados neste root.
Fixtures em `.tftest.hcl` são apenas mocks, sem existência AWS alegada.

Um perfil de plataforma `AWSLambda-SHA384-ECDSA` existente e aprovado deverá produzir
a assinatura do ZIP revisado fora desta execução. Confirmar sua plataforma, versão,
status/revogação e validade no fluxo autorizado. O formato do ARN sozinho não prova
essas propriedades. A verificação criptográfica de deployment cabe à Lambda; o
Terraform local não demonstra validade da assinatura nem substitui o AWS Signer.

O build determinístico continua produzindo o ZIP **não assinado**. Ele é insumo da
assinatura, não artefato implantável sob Enforce. Após assinatura, inspecionar/smoke-testar
o ZIP final, calcular novo SHA-256 e preencher `artifact_path`/`artifact_sha256` com esse
arquivo. Não reempacotar o ZIP assinado nem usar o checksum do ZIP original.
O SHA-256 identifica bytes; não autentica o publicador.

IAM do runtime, VPC privada, AWS_IAM, endpoint HTTP desabilitado, concorrência 0/2,
retenção de 7 dias, segredo fora de state e TLS verify-full foram preservados.
Permissões de assinatura/deployment devem ser separadas e restritas; não entregar ao
runtime capacidade de assinar, atualizar código ou alterar a configuração de assinatura.

## PostgreSQL e contrato Java

Os 16 skips vêm de `pytest.mark.skipif(not TEST_DATABASE_URL)` em `test_postgres.py`.
O CI define DSN e rejeita qualquer skip no XML; não há fallback para banco fake.
Neste sandbox: Docker falha com permission denied; `initdb`/servidor ausentes;
bind TCP e Unix falham com Operation not permitted. A tentativa de preparação local
falhou em `psycopg.connect` antes de executar SQL. Não foi usado RDS ou outro banco.

A inspeção dos quatro arquivos Java confirma o contrato:

1. Bootstrap 01 cria `oficina_lambda` LOGIN INHERIT e revoga CREATE público e TEMP
   público no banco `oficina_db`; migrator/API são separados.
2. V1–V8 executam como `oficina_migrator`. V8 cria `auth.customer_access` com default
   false, view com barreira e exclusão de CPF ambíguo; não há `customers.active`.
3. Bootstrap reader executa como **administrador**, com CREATEROLE e capacidade de
   grant sobre objetos do migrator. O comentário genérico do arquivo reader não deve
   ser interpretado como permissão para executar usando `oficina_migrator` sem CREATEROLE.
4. Grants 02 concedem somente `oficina_auth_reader` a `oficina_lambda`; essa role
   NOLOGIN tem USAGE em auth e SELECT apenas na view autorizada.

A auditoria de privilégios no teste existente foi ampliada para buscar permissões
indevidas em **todas as tabelas/views de aplicação**, inclusive grants PUBLIC/herdados/
por coluna, escrita na view, sequências, CREATE em schemas e CREATE/TEMP no banco.
Permanecem testes de leitura proibida de customers/app_users/customer_access, escrita,
DDL, SET ROLE indevido, TLS/CA/hostname, elegibilidade e allowlist sintética.
**Essas verificações SQL foram preparadas, não comprovadas em execução nesta sessão.**
Nenhum arquivo irmão foi modificado e os hashes Java continuam iguais aos do Prompt 12.

## JAVA_CONTRACT_SHA: procedimento atual após reconciliação

PR Java #7 integrado: `JAVA_CONTRACT_SHA=57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`.
O pin está no arquivo raiz e no workflow; não depende de variável remota. O script
verifica ancestralidade em `origin/main` e usa o conteúdo imutável do commit.

Procedimento atual: `bash scripts/validate_external.sh ../tech-challenge-oficina`.
Exige PostgreSQL/TLS sem skips, query V2 no ZIP, smoke Python 3.13 e 11 testes
Terraform mock. Consulte o [fechamento V2](fase3/lambda-v2-final.md) para requisitos
e bloqueadores. Os resultados e comandos manuais abaixo são históricos; o script
atual substitui a sequência manual e mantém artefatos/fixtures em diretórios novos.

## Resultados desta execução

| Verificação | Resultado efetivo |
| --- | --- |
| pytest completo / Python 3.12.3 local | **70 aprovados, 16 ignorados**, 0 falhas nos executados; XML `dist/tests-prompt13.xml` |
| Integração PostgreSQL/TLS | **Bloqueada**, nenhum cenário SQL executado; tentativa de conexão local falhou antes do bootstrap |
| Ruff | Aprovado |
| YAML CI / ShellCheck | Aprovados |
| Checkov 3.2.471 antes | 20 aprovados, **8 falhas**, 0 skips, 0 erros de parsing |
| Checkov 3.2.471 depois | 21 aprovados, **7 falhas**, 0 skips, 0 erros de parsing; exit code 1 |
| Terraform fmt -check -recursive | Aprovado |
| Terraform validate | **Bloqueado** ao iniciar o provider AWS, socket Unix proibido; não passou |
| Terraform test | **Bloqueado**, 0 cenários executados dos 11 runs exclusivamente mock; não passou |
| Expressão de validação de ARNs / Terraform console sem provider | 6 cenários aprovados: válido, vazio, sem versão, conta errada, região errada, wildcard |
| ZIP | Inspeção de layout, bibliotecas nativas e padrões de segredo aprovada no ZIP não assinado do Prompt 12 |
| Smoke Python 3.13 Docker | Continua bloqueado pelo daemon inacessível; não coberto pelo pytest Python 3.12 |
| git diff --check | Aprovado |

O ZIP inspecionado, ainda não implantável, tem SHA-256
`2b1baa9f7bcc3dd543dffb7da042d8224e7524190eb3000563d27f5f67e87549` e identificador
sintético de 40 zeros. Código runtime/dependências não mudaram neste prompt.
Nenhum artefato foi assinado, nenhuma chave operacional foi criada e nenhum controle
foi relaxado para aprovar ferramenta.

## Arquivos alterados neste prompt

- `.github/workflows/ci.yml`: confirmação do HEAD Java após checkout.
- `infra/terraform/main.tf`: associação/configuração obrigatória de code signing.
- `infra/terraform/variables.tf`: confiança em versões Signer e contrato do ZIP assinado.
- `infra/terraform/tests/closed.tftest.hcl`: enforcement e rejeição de perfis inválidos.
- `tests/test_infrastructure.py`: regressão de enforcement e ausência de privilégios Signer no runtime.
- `tests/test_postgres.py`: auditoria ampliada de privilégios SQL efetivos.
- `README.md`, `docs/infra-staging.md`: fluxo atual e requisito de assinatura.
- `docs/prompt13-security-integration.md`: este relatório e roteiro externo.

As demais diferenças já existentes no working tree pertencem aos prompts anteriores.

## Bloqueios para plan/deployment

1. Rodar PostgreSQL/TLS e smoke Python 3.13 em ambiente permitido; executar Terraform
   validate/test com o provider fixado. Nenhum skip pode substituir essa evidência.
2. Publicar/revisar o contrato Java e fixar seu SHA real no CI.
3. Revisar as sete falhas remanescentes: três decisões arquiteturais (itens 1/3/5/7,
   quatro ocorrências) e CMK própria ainda não implementada (itens 2/6/8).
   Não há aprovação automática de exceções neste relatório.
4. Preparar release assinada, versão de perfil confiável e checksum final, além dos
   outputs/conta reais, backend protegido e IAM de deployment. Esses são insumos do
   futuro plan; não se exige que RDS já esteja provisionado para revisar código local.
5. Para ativar a janela, depois das etapas autorizadas: endpoint/secret ARN coerentes,
   bootstrap/grants aplicados, pública/kid/issuer/audience Java e fixtures sintéticas.
   Verificação em AWS é futura e não foi realizada.

## Comandos fora do sandbox

Executar em runner isolado com Python 3.13, Docker permitido, PostgreSQL 16 local,
Terraform 1.14.6 e provider AWS 6.38.0. A seção seguinte não executa AWS nem plan real.
Use venv e dependências conforme o README; a instalação de ferramentas/pull de imagens,
quando necessária, é preparação externa. Não apontar `TEST_DATABASE_URL` para banco
compartilhado, túnel RDS ou qualquer serviço AWS.

Preparação PostgreSQL equivalente ao CI, com imagem já disponível no runner. Definir
`JAVA_PRODUCER` para o clone da revisão Java a validar. O bloco usa container/porta
efêmeros, publica só em loopback e limpa apenas suas fixtures. A senha literal é
exclusivamente do banco descartável de teste, como no CI:

```sh
(
set -euo pipefail
local_tls=$(mktemp -d)
pg_id=''
cleanup() {
  if [ -n "$pg_id" ]; then docker stop --time 5 "$pg_id" >/dev/null; fi
  python - "$local_tls" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
for name in ('server.crt', 'server.key'):
    (root / 'tls' / name).unlink(missing_ok=True)
if (root / 'tls').exists():
    (root / 'tls').rmdir()
root.rmdir()
PY
}
trap 'cleanup' EXIT
python scripts/prepare_test_tls.py "$local_tls/tls"
pg_id=$(docker run --rm --detach --pull=never \
  -p 127.0.0.1::5432 -e POSTGRES_DB=oficina_db \
  -e POSTGRES_PASSWORD=local-test-only postgres:16-alpine)
pg_port=$(docker port "$pg_id" 5432/tcp)
pg_port=${pg_port##*:}
for _ in $(seq 1 30); do
  if docker exec "$pg_id" pg_isready -U postgres -d oficina_db >/dev/null 2>&1; then break; fi
  sleep 1
done
docker cp "$local_tls/tls/server.crt" "$pg_id:/tmp/server.crt"
docker cp "$local_tls/tls/server.key" "$pg_id:/tmp/server.key"
docker exec "$pg_id" chown postgres:postgres /tmp/server.crt /tmp/server.key
docker exec "$pg_id" chmod 600 /tmp/server.key
docker exec "$pg_id" psql -U postgres -d oficina_db -v ON_ERROR_STOP=1 \
  -c "ALTER SYSTEM SET ssl_cert_file = '/tmp/server.crt'" \
  -c "ALTER SYSTEM SET ssl_key_file = '/tmp/server.key'" \
  -c "ALTER SYSTEM SET ssl = on" -c 'SELECT pg_reload_conf()'
export TEST_DATABASE_URL="host=127.0.0.1 port=$pg_port dbname=oficina_db user=postgres password=local-test-only sslmode=verify-full"
export PGSSLROOTCERT="$local_tls/tls/server.crt"
python - <<'PY'
import os
import time
import psycopg
for attempt in range(20):
    try:
        with psycopg.connect(os.environ['TEST_DATABASE_URL'], connect_timeout=2):
            break
    except psycopg.OperationalError:
        if attempt == 19:
            raise
        time.sleep(0.5)
PY
python scripts/prepare_integration.py "$JAVA_PRODUCER" --disposable
python -m pytest -q -ra --junitxml=dist/tests-external.xml
python - <<'PY'
from xml.etree import ElementTree
root = ElementTree.parse('dist/tests-external.xml').getroot()
assert not root.findall('.//skipped'), 'Integrações obrigatórias foram ignoradas'
PY
cleanup
trap - EXIT
)
```

Ao sair, o banco é removido sem volume persistente. Demais validações no checkout Lambda:

```sh
ruff check auth scripts tests
checkov -d infra/terraform --framework terraform --skip-download
python scripts/prepare_terraform_fixture.py
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
python scripts/validate_zip.py "$ZIP_TO_VALIDATE"
git diff --check
```

Checkov continuará retornando 1 pelos sete achados: não acrescentar `--soft-fail` ou
skips para disfarçá-los. Não executar `plan` real, apply/destroy, import ou refresh como
parte desta validação. `terraform test` usa somente mock_provider e `command=plan`;
a fixture ZIP é não implantável e o state mock é efêmero.

Smoke do ZIP extraído em diretório temporário, com a imagem já disponível localmente:

```sh
bundle_dir=$(mktemp -d)
unzip -q "$ZIP_TO_VALIDATE" -d "$bundle_dir"
docker run --rm --pull=never --network none --entrypoint python \
  -v "$bundle_dir:/var/task:ro" \
  -v "$PWD/scripts/smoke_bundle.py:/tmp/smoke.py:ro" \
  -e PYTHONPATH=/var/task \
  public.ecr.aws/lambda/python@sha256:cf3e082b6c28721944dfef2c252c2e6df2f65aab3c2e2a64ce1f86f80c7aa13d \
  /tmp/smoke.py
```

Repetir inspeção e smoke no ZIP final assinado, sem modificar seus bytes. Os comandos
acima validam conteúdo/runtime, não autenticam uma assinatura AWS Signer localmente.
Encerrado para revisão; **nenhum deployment autorizado ou realizado**.
