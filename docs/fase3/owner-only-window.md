# Janela manual com A: CPF → JWT → 200/401/403

**Preparação somente. Nenhum comando de abertura, patch ou rollout foi executado.**
O operador confirmou A sintética/ativa, na allowlist, com quatro OS próprias em
staging. O 404 cruzado está **PENDENTE**: não existe OS de outro cliente sintético
confirmada. A suíte completa continua padrão (`--scope full`) e preserva todos os
cenários. `--scope owner-only` é demonstração parcial explícita; seu exit 0 nunca
significa aprovação da suíte completa. Os demais negativos da suíte completa
(inativo/ausente/malformado/assinatura adulterada etc.) também ficam NOT_RUN.

## Bloqueio ADMIN e revisão atual

[Leitura de confiança](java-owner-window-trust-evidence.json), em
2026-11-03T21:08:04Z: ConfigMap resourceVersion 6362; PEM desabilitado, zero chaves;
issuer/audience iguais à Lambda, kid presente no JWKS HTTPS, RSA 3072.
[Pods atuais](owner-window-node-readonly.json): 3 Ready, imageID
`sha256:8ca6d21e1a7723b59cb68268bf1dce683a0b3a951de7e14a646e1a5d5267e00b`,
diferente do snapshot anterior. Essa leitura é configuração declarada, não prova
carregamento pelo JVM nem a resposta 403 do novo digest. Confirmar com o responsável
da API que a imagem aprovada mantém a cadeia PEM auditada antes de aplicar patch.
Nenhum arquivo Java da sessão 2 foi alterado.

O [patch atual](java-admin-forward.json) testa resourceVersion e todo o `data`
anterior, preserva issuer/audience/JWKS e modifica só customer-jwt.yml + nova chave
**pública** lambda-current-public.pem. O [patch de reversão](java-admin-reverse.json)
testa o `data` posterior esperado e restaura exatamente o [snapshot anterior](java-admin-before.json),
inclusive removendo a chave adicionada. Não usar o antigo merge-patch nesta janela.
Mudança concorrente causa falha; não remover os testes do patch para forçar aplicação.

Aplicação e rollout abaixo são **etapa futura separadamente autorizada**. Enquanto
não concluídos e verificados, o 403 segue bloqueado; manter Lambda zero. A preparação
não contorna esse bloqueio aceitando 401. A reversão requer outro rollout porque o
verificador PEM carrega configuração/chave na inicialização; apenas restaurar o
ConfigMap não remove a confiança já carregada por pods existentes.

## 1. UBUNTU LOCAL — ambiente e acesso

Credenciais preexistentes no perfil oficina-staging, CA RDS aprovada e venv já
instalado. O bridge temporário abaixo resolve a compatibilidade CLI/boto3 sem
imprimir credenciais; foi revisado e não grava tokens em arquivos.
Em cada terminal local:

```bash
set -euo pipefail
set +x
cd /tmp/oficina-security-review/lambda-docs
source /tmp/lambda-v2-validation.bbK2ghVL/venv/bin/activate
stage() { bash scripts/with_staging_profile.sh "$@"; }
aws_stage() { stage aws --profile oficina-staging --region us-east-2 "$@"; }
test "$(aws_stage sts get-caller-identity --query Account --output text)" = 797781632267
test "$(aws_stage lambda get-function-concurrency --function-name oficina-staging-auth --query ReservedConcurrentExecutions --output text)" = 0
test "$(aws_stage apigatewayv2 get-api --api-id t6p562hkc9 --query DisableExecuteApiEndpoint --output text)" = True
OFICINA_REVIEW_SHA=$(git rev-parse HEAD)
printf 'Commit para conferir nos outros hosts: %s\n' "$OFICINA_REVIEW_SHA"
```

Não usar checkout com alterações locais. Conferir commit e CI aprovados antes da
execução; congelar alterações de Lambda/configuração/deployment durante a janela.
Em terminal dedicado, abrir sessão administrativa privada já autorizada:

```bash
aws_stage ssm start-session --target i-0ab373b1ba5c14b3e
```

## 2. EC2 VIA SSM — patch e rollout futuros, somente após autorização própria

Transferir somente os dois patches públicos do commit revisado. Não transferir
fixture, CPF, senha, JWT ou private key para comandos SSM. O download abaixo usa
URLs HTTPS imutáveis; os testes JSON impedem aplicação sobre outro ConfigMap.

```bash
set -euo pipefail
set +x
umask 077
read -r -p 'SHA completo aprovado da Lambda/docs: ' OFICINA_REVIEW_SHA
[[ "$OFICINA_REVIEW_SHA" =~ ^[0-9a-f]{40}$ ]]
OFICINA_PATCH_DIR=$(mktemp -d /tmp/oficina-admin-review.XXXXXXXX)
for patch in java-admin-forward.json java-admin-reverse.json; do
  curl --fail --silent --show-error --proto '=https' --proto-redir '=https' \
    "https://raw.githubusercontent.com/ppdssdev/tech-challenge-oficina-auth-lambda/$OFICINA_REVIEW_SHA/docs/fase3/$patch" \
    -o "$OFICINA_PATCH_DIR/$patch"
done
sudo k3s kubectl -n oficina-staging get deployment oficina-api \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}{"\n"}'
sudo k3s kubectl -n oficina-staging patch configmap oficina-customer-jwt \
  --type=json --patch-file "$OFICINA_PATCH_DIR/java-admin-forward.json" --dry-run=server -o name
```

O dry-run não habilita nada. Após revisão/autorização explícita de configuração e
rollout, no mesmo terminal EC2 (não executado nesta entrega):

```bash
sudo k3s kubectl -n oficina-staging patch configmap oficina-customer-jwt \
  --type=json --patch-file "$OFICINA_PATCH_DIR/java-admin-forward.json" -o name
sudo k3s kubectl -n oficina-staging rollout restart deployment/oficina-api
sudo k3s kubectl -n oficina-staging rollout status deployment/oficina-api --timeout=180s
sudo k3s kubectl -n oficina-staging get pods -l app=oficina-api \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,IMAGEID:.status.containerStatuses[*].imageID
```

Exigir pods novos Ready e mesmo digest aprovado. Se falhar, não abrir Lambda;
executar reversão da seção 8. Não atualizar imagem, IAM, Terraform, allowlist ou DB.
Guardar OFICINA_PATCH_DIR até encerrar/reverter; não usar rollout undo genérico.

## 3. EC2 VIA SSM + UBUNTU LOCAL — dois túneis privados

Depois do rollout aprovado, em sessão EC2 dedicada:

```bash
read -r -p 'Nome do pod Ready com digest aprovado: ' OFICINA_POD
sudo k3s kubectl -n oficina-staging port-forward --address 127.0.0.1 \
  "pod/$OFICINA_POD" 18080:8080
```

UBUNTU LOCAL, outro terminal com preâmbulo da seção 1:

```bash
test -z "$(ss -H -ltn '( sport = :18080 )')"
aws_stage ssm start-session --target i-0ab373b1ba5c14b3e \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18080"],"localPortNumber":["18080"]}'
```

UBUNTU LOCAL, outro terminal com o mesmo preâmbulo:

```bash
test -z "$(ss -H -ltn '( sport = :15432 )')"
OFICINA_RDS_HOST=$(aws_stage rds describe-db-instances --db-instance-identifier oficina-staging-postgres --query 'DBInstances[0].Endpoint.Address' --output text)
test "$(aws_stage rds describe-db-instances --db-instance-identifier oficina-staging-postgres --query 'DBInstances[0].PubliclyAccessible' --output text)" = False
OFICINA_RDS_PARAMS=$(python -c 'import json,sys; print(json.dumps({"host":[sys.argv[1]],"portNumber":["5432"],"localPortNumber":["15432"]}))' "$OFICINA_RDS_HOST")
aws_stage ssm start-session --target i-0ab373b1ba5c14b3e \
  --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "$OFICINA_RDS_PARAMS"
```

No terminal principal UBUNTU LOCAL, conferir `ss -ltnp '( sport = :18080 or sport = :15432 )'`:
listeners exclusivamente 127.0.0.1, processos session-manager-plugin esperados,
sem outro usuário/processo controlando as portas. Atestar conta/alvo/pod/digest e
host controlado; a presença de socket não autentica seu processo sozinha.
SSM não cria HTTPS no listener Java: o trecho final é HTTP. JWKS continua HTTPS;
RDS conserva host DNS, hostaddr loopback, CA e verify-full. Não usar TLS inseguro.

## 4. UBUNTU LOCAL principal — preparar A e preflight sem abrir

O builder lê os arquivos privados existentes de A e escolhe a primeira das quatro
OS já confirmadas. Não imprime valores, não lê Secrets, não pede auditor, não
invoca Lambda e grava somente entrada privada local. Não atestar ADMIN pronto
antes de concluir a etapa 2. Caso o snapshot de JWKS/ConfigMap mude, reavaliar o
patch; não aceitar automaticamente uma nova origem de confiança.

```bash
OFICINA_PRIVATE=$(mktemp -d "$HOME/.config/oficina/staging/owner-window.XXXXXXXX")
export OFICINA_OWNER_INPUT="$OFICINA_PRIVATE/owner.json"
stage python -m scripts.prepare_owner_input --output "$OFICINA_OWNER_INPUT"
stage python -m scripts.cpf_demo preflight --scope owner-only --authorized \
  --input "$OFICINA_OWNER_INPUT"
```

O preflight mantém gates de conta/release assinada/VPC/Gateway fechado/IAM/allowlist,
verifica A ativa em V2 e privilégios reader/TLS por SELECT read-only. Senha reader
somente no prompt oculto. Ownership/proveniência são atestações apoiadas no inventário
já executado, não novas consultas nas tabelas públicas pelo reader. Não repetir o
comando de criação sobre arquivo existente: ele recusa sobrescrita.

## 5. OUTRO HOST UBUNTU — recuperação independente ANTES da janela

Host independente (não somente outra aba do host principal), perfil próprio já
válido, mesmo commit e dependências instalados. Sem JSON/CPF/senha de banco. Precisa
permissão preexistente para Put/GetFunctionConcurrency; falha impede abertura.
Nesta sessão, entrar no checkout aprovado e ativar o venv existente:

```bash
set -euo pipefail
set +x
read -r -p 'Checkout do mesmo commit aprovado neste host: ' OFICINA_CHECKOUT
read -r -p 'Venv aprovado neste host: ' OFICINA_VENV
cd "$OFICINA_CHECKOUT"
source "$OFICINA_VENV/bin/activate"
git rev-parse HEAD
bash scripts/with_staging_profile.sh python -m scripts.cpf_demo watchdog --authorized
```

Digitar consentimento oculto. Só considerar armado após
`watchdog_armed_240_seconds=PASS`. Iniciar o principal em até 30 segundos dessa linha.
Se atrasar, não abrir: esperar fechamento e armar novamente. Manter o watchdog
vivo até final_concurrency_zero=PASS mesmo se o principal terminar cedo.
Não compartilhar o ciclo de vida do terminal/túnel principal. Conferir duração das
credenciais para cobrir janela e recuperação; não sobrepor janelas.

## 6. UBUNTU LOCAL principal — janela futura autorizada

Somente depois de autorização operacional própria e do watchdog armado:

```bash
stage python -m scripts.cpf_demo window --scope owner-only --authorized \
  --recovery-armed --input "$OFICINA_OWNER_INPUT"
```

O flag recovery-armed é atestação do operador, não detecta outro host. O script
repete preflight; prazo de 180 s inclui prompts/credenciais/preflight e impede
abertura tardia. Abre concorrência 2 só após gates, emite JWT A por Invoke direto,
valida RS256/JWKS/issuer/audience/kid/TTL900 e faz exatamente:

1. GET `/api/v1/customer/work-orders/{OS_A}` com JWT A → **200** e ID da OS esperado.
2. O MESMO GET sem Authorization → **401**.
3. GET `/api/v1/admin/work-orders` com o MESMO JWT A → **403**.

Tokens ficam em memória; saída só nomes de checks/status. Invoke direto não prova
HTTP/SigV4 do Gateway, que permanece fechado. ADMIN 401 é FAIL. O resultado registra
scope owner-only, full_suite NOT_RUN, cross_customer_404 PENDING. Não declarar
Fase 3/suíte completa aprovadas. Não modificar o JSON para simular B/C.

## 7. Recuperação e confirmação final — duas vias

finally do principal chama Put(0) e exige Get=0, inclusive se a abertura sofreu
timeout ambíguo ou o smoke falhar. Cleanup tem orçamento separado de 120 s;
watchdog fecha em 240 s. SIGKILL/perda de host exige a via independente. Os 180 s
não garantem que a confirmação zero chegue nesse exato instante.

Se o principal falhar ou não confirmar zero, OUTRO HOST UBUNTU, imediatamente:

```bash
bash scripts/with_staging_profile.sh python -m scripts.cpf_demo close --authorized
```

Não esperar os 240 s quando recuperação imediata for necessária. Esse comando
só fecha, nunca usa DeleteFunctionConcurrency e independe da entrada privada.
Se ambas as vias perderem AWS, estado DESCONHECIDO, nunca PASS; repetir fechamento
quando acesso voltar. Uma recuperação tardia não transforma smoke falho em sucesso.

Após principal e watchdog concluírem, em CADA host (mesmo checkout/bridge):

```bash
test "$(bash scripts/with_staging_profile.sh aws --profile oficina-staging --region us-east-2 lambda get-function-concurrency --function-name oficina-staging-auth --query ReservedConcurrentExecutions --output text)" = 0
test "$(bash scripts/with_staging_profile.sh aws --profile oficina-staging --region us-east-2 apigatewayv2 get-api --api-id t6p562hkc9 --query DisableExecuteApiEndpoint --output text)" = True
printf 'PASS: leitura final Lambda zero e Gateway fechado\n'
```

Registrar UTC, commit, digest, 200/401/403, pending 404 e ambas confirmações zero.
Não publicar entrada privada/hash/CPF/JWT/credenciais. JWT já emitido pode valer
até 900 s; concorrência zero não o revoga. Encerrar túneis com Ctrl-C depois.

## 8. EC2 VIA SSM — reversão preparada, execução futura autorizada

Com Lambda zero confirmada, usar OFICINA_PATCH_DIR da seção 2:

```bash
sudo k3s kubectl -n oficina-staging patch configmap oficina-customer-jwt \
  --type=json --patch-file "$OFICINA_PATCH_DIR/java-admin-reverse.json" --dry-run=server -o name
sudo k3s kubectl -n oficina-staging patch configmap oficina-customer-jwt \
  --type=json --patch-file "$OFICINA_PATCH_DIR/java-admin-reverse.json" -o name
sudo k3s kubectl -n oficina-staging rollout restart deployment/oficina-api
sudo k3s kubectl -n oficina-staging rollout status deployment/oficina-api --timeout=180s
sudo k3s kubectl -n oficina-staging get configmap oficina-customer-jwt -o json | \
  python3 -c 'import json,sys; current=json.load(sys.stdin)["data"]; original=json.load(open(sys.argv[1]))[-1]["value"]; assert current==original; print("PASS: ConfigMap original restaurado")' \
  "$OFICINA_PATCH_DIR/java-admin-reverse.json"
sudo k3s kubectl -n oficina-staging get pods -l app=oficina-api \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,IMAGEID:.status.containerStatuses[*].imageID
```

Confirmar todos os pods renovados/Ready no digest aprovado e repetir leitura Lambda
zero nos hosts operadores. Se o patch de ida não chegou a aplicar, a reversão pode
falhar no teste por já estar no estado original; confirmar comparação read-only
antes de concluir. Não contornar falha por divergência intermediária.

## Testes locais e limites

A suíte original do smoke continua integral, com testes adicionais do modo parcial:
A sem B/C é aceito apenas no opt-in; full continua rejeitando ausência/allowlist
incompleta; três GETs exatos, assinatura real, issuer inválido bloqueado antes de
HTTP e ADMIN 401 reprovado. AWS/HTTP são mocks e sockets/DNS proibidos nos testes.
Forward/reverse conferidos localmente: somente duas chaves modificadas e restauração
exata do data anterior. Nenhum desses testes prova 403 no pod atual.

Comandos operacionais acima, builder de entrada e bridge não foram executados nesta
preparação. Leituras AWS/SSM/JWKS atuais estão nos snapshots; sem Secrets, SQL staging,
Invoke, janela, rollout, patch aplicado, IAM/Terraform ou alteração de allowlist.

Validação completa local desta revisão: **206 testes passaram, zero falhas/erros/skips**,
Python 3.13.18, PostgreSQL 16.15 descartável com TLS verify-full; cleanup confirmado.
Inclui 97 testes do smoke (92 originais + 5 novos) e 1 do inventário. Ruff, Bash,
ShellCheck, diff-check e inversão exata dos patches passaram. Sem execução staging.
