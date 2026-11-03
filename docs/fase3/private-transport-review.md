# Revisão do transporte privado do smoke CPF

Preparação corretiva de Lambda #6, sem execução da janela ou alteração de Java,
AWS, IAM, banco ou Terraform. Preserva o [runbook](cpf-demo-runbook.md) e a
[matriz original](cpf-demo-evidence.md). Integrar a documentação de Lambda #7
**depois de #6 e desta correção**, conciliando o README com a frente proprietária.

## Bloqueio encontrado e contrato do adapter

O Java versionado expõe HTTP :8080 por ClusterIP. `validate_inputs` e `http_get`
exigiam HTTPS também para Java; SSM não transforma esse listener em HTTPS. Trocar
`https://` por `http://` no JSON anterior falhava antes da janela. Não há evidência
nesta revisão de um listener HTTPS Java privado já aprovado.

O novo adapter é uma opção explícita, restrita a `http://127.0.0.1:18080`, usando
`java_transport="ssm-loopback"` e `ssm_tunnel_attested=true`. Não aceita `localhost`,
IPv6, outro IP/porta, prefixo, credenciais na URL, query, fragmento ou HTTP remoto.
Envia somente GET nas duas rotas do smoke, com UUID canônico na rota CUSTOMER;
não usa proxy, não segue redirects e limita a resposta a 64 KiB. HTTPS permanece
o padrão. Não há flag genérico para desligar TLS.

A atestação é humana: **o código não consegue provar que o listener loopback é
um túnel SSM legítimo**. Antes de defini-la, conferir identidade AWS, sessão SSM,
instância, listener exclusivo, pod escolhido e seu digest. Host local compartilhado,
listener desconhecido, troca de pod ou túnel sem prova bloqueiam a execução.
Os sockets loopback não autenticam processos locais; executar em host controlado.

Caminho proposto: cliente → loopback → canal autenticado SSM → loopback do nó →
`kubectl port-forward` autenticado ao Kubernetes → porta HTTP do pod. O trecho
final port-forward→pod é HTTP; **não é HTTPS fim a fim com Java**. Não remover
validação de CA/hostname de Kubernetes ou descrever SSM como certificado Java.
Se o aceite exigir TLS até o listener Java, este adapter não satisfaz esse aceite:
registrar bloqueio de topologia para outra frente, sem mudar Java nesta revisão.

`jwks_url` e `issuer` continuam exigindo HTTPS. O JWKS é obtido com validação de
certificado/hostname e RS256, chave, kid, issuer, audience, tempos/TTL e claims são
verificados exatamente como antes. O adapter não altera os aceites: OS própria
200, ausência de token 401, CUSTOMER em ADMIN **403**, OS alheia **404**. O possível
401 da cadeia ADMIN PEM versus CUSTOMER JWKS continua FAIL, não equivalência.

## Comandos por ambiente — proposta, não executados aqui

Requer AWS CLI e Session Manager plugin já aprovados, autorização de sessão e
permissões preexistentes. Não criar permissões, kubeconfig, endpoint ou listener
público. Estes comandos apenas estabelecem acesso privado temporário; não abrem
Lambda. Não inserir CPF, JWT ou senha em comandos, arquivos SSM ou logs.

No host do operador, validar identidade e alvo aprovado; os valores de referência
não substituem a consulta atual:

```bash
set -euo pipefail
set +x
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
aws_stage() { aws --profile oficina-staging --region us-east-2 "$@"; }
export OFICINA_NODE=i-0ab373b1ba5c14b3e
test "$(aws_stage sts get-caller-identity --query Account --output text)" = 797781632267
aws_stage ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$OFICINA_NODE" \
  --query 'InstanceInformationList[].{Instance:InstanceId,Status:PingStatus}'
aws_stage ssm start-session --target "$OFICINA_NODE"
```

Exigir conta `797781632267`, alvo validado e status Online. Dentro da sessão SSM,
identificar pod Ready da API e conferir imagem/digest previamente aprovados:

```bash
sudo k3s kubectl -n oficina-staging get pods -l app=oficina-api \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[*].ready,IMAGE:.spec.containers[*].image,IMAGEID:.status.containerStatuses[*].imageID
read -r -p 'Pod aprovado: ' OFICINA_POD
sudo k3s kubectl -n oficina-staging port-forward --address 127.0.0.1 \
  "pod/$OFICINA_POD" 18080:8080
```

Não usar `--insecure-skip-tls-verify` ou exportar kubeconfig administrativo.
Manter a sessão viva. Em outro terminal do host do operador, repetir o preâmbulo `aws_stage` acima:

```bash
aws_stage ssm start-session --target "$OFICINA_NODE" \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18080"],"localPortNumber":["18080"]}'
```

Em terceiro terminal, conferir que a porta pertence ao processo esperado e é
exclusivamente loopback; parar diante de bind wildcard ou processo desconhecido:

```bash
ss -ltnp '( sport = :18080 )'
```

A entrada privada usa estas propriedades adicionais, mantendo os demais valores
do inventário aprovado; não publicar o JSON completo:

```json
{
  "java_url": "http://127.0.0.1:18080",
  "java_transport": "ssm-loopback",
  "ssm_tunnel_attested": true
}
```

Para RDS, manter `db_host` igual ao DNS RDS aprovado e a CA confiável. Em outro
terminal (com o mesmo preâmbulo `aws_stage`), preencher apenas esse hostname a partir da inspeção autorizada:

```bash
read -r -p 'DNS RDS aprovado: ' OFICINA_RDS_HOST
aws_stage ssm start-session --target "$OFICINA_NODE" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "$(jq -cn --arg host "$OFICINA_RDS_HOST" \
    '{host:[$host],portNumber:["5432"],localPortNumber:["15432"]}')"
```

Conferir `ss -ltnp '( sport = :15432 )'` da mesma forma. Definir no JSON privado
`"db_transport": "ssm-loopback"`. O script usa **host=DNS RDS**,
**hostaddr=127.0.0.1**, porta 15432, `sslmode=verify-full` e `sslrootcert` aprovado.
Isso conserva a identidade do certificado RDS, inclusive através do túnel.
Não trocar `db_host` por localhost, remover TLS nem usar senha em argv/env.
Sem esse modo, mantém conexão TLS direta ao DNS RDS:5432.

Depois de atestar os túneis, executar apenas o preflight autorizado em host privado:

```bash
python -m scripts.cpf_demo preflight --authorized --input /caminho/privado/demo.json
```

A/B/C/D não são presumidos existentes. Ausência no SELECT V2, grant extra,
allowlist incompatível, falta de OS/proveniência ou CA inválida bloqueiam a janela;
nenhum bootstrap é executado. O preflight não prova os GETs Java nem emissão.
Fechar as sessões de port-forward com Ctrl-C após o uso. Para a futura janela,
seguir exatamente watchdog independente, janela e dupla confirmação do runbook.

## Limites de tempo e recuperação

O processo principal conserva 180 segundos desde seu início, incluindo prompts,
credenciais e preflights. O teste monotônico antes de abrir impede abertura tardia.
O watchdog independente é armado antes, espera 240 segundos e fecha; iniciar o
principal em até 30 segundos após a evidência de armação, conforme o runbook.

A correção acrescenta orçamento **separado de 120 segundos para cleanup**, além
das três tentativas SDK. Antes, cleanup cancelava o alarme e ignorava SIGALRM;
DNS/refresh de credenciais podiam prolongá-lo indefinidamente. Agora o sinal de
prazo sai do handler de retries, emite FAIL sem valores e exige recuperação
independente. SIGINT/SIGTERM/SIGHUP continuam ignorados apenas durante cleanup.
Assim 180 s limita o trabalho principal, não garante concorrência zero exatamente
naquele instante: fechamento depende de AWS disponível, resposta e leitura Get=0.
SIGKILL, falha de host ou rede continuam fora da garantia local. Mesmo após Put(0),
sem leitura Get=0 o estado é **desconhecido**, e não PASS. Não iniciar nova janela.

O operador independente deve conseguir repetir `python -m scripts.cpf_demo close
--authorized` em outra sessão/host, sem dados de fixtures. Uma recuperação após
prazo não retroativamente aprova a demonstração; registrar falha e horários.

## Verificação local

Primeira execução desta correção: **92 testes do smoke passaram**; suíte Python local
**178 passaram / 22 PostgreSQL ignorados**, com `TEST_DATABASE_URL` removida
explicitamente para impedir acesso a banco. Ruff (`auth scripts tests`) e
`git diff --check` passaram. Os 22 testes exigem PostgreSQL descartável; a execução complementar abaixo
os comprovou localmente. Nenhum foi executado no RDS.

Os testes proíbem sockets/DNS; AWS, HTTP e SQL são simulados. Exercitam origens e
rotas proibidas, opt-in, ausência de redirects/proxies, CA/hostname HTTPS, TLS RDS
preservando DNS, prazo de cleanup sem falso PASS e a matriz criptográfica anterior.
Não comprovam transporte SSM, certificado operacional, fixtures ou comportamento
Java implantado. Nenhuma exceção foi adicionada à verificação RS256 ou ao 403 ADMIN.

## Bloqueio de allowlist observado em staging

Consulta AWS somente leitura em `2026-11-03T17:11:21Z` contou **um** ID distinto
em `AUTH_SYNTHETIC_CUSTOMER_IDS` da Lambda `oficina-staging-auth`. Nenhum ID foi
publicado. O smoke exige três clientes distintos (`owner`, `other`, `inactive`)
e verifica presença de todos em `preflight`, gate `synthetic_allowlist`; logo,
o preflight completo está **bloqueado pela configuração atual**, independentemente
da existência de OS. A contagem não comprova fixture ou proveniência no RDS.

O plano mínimo futuro precisa contemplar inventário privado e allowlist aprovada
para três identidades distintas; não basta criar dados. Nenhuma alteração de
allowlist, Lambda ou banco foi autorizada/executada nesta revisão.

Para repetir somente a contagem, no host do operador com o preâmbulo aprovado
(`aws_stage`), sem imprimir a variável completa:

```bash
aws_stage lambda get-function-configuration --function-name oficina-staging-auth \
  --query Environment.Variables.AUTH_SYNTHETIC_CUSTOMER_IDS --output text | \
  python3 -c 'import json,sys; ids=json.load(sys.stdin); print(json.dumps({"allowlist_count":len(ids),"distinct":len(set(ids))}))'
```

## Execução complementar: suíte Python sem skips

No commit `09f3b0d0b51ef17545cbbc6be32223497b434863`, a suíte completa passou:
**200 testes, zero falhas/erros/skips**, Python 3.13.18 e PostgreSQL 16.15 com TLS
`verify-full` real. Inclui os 92 testes do smoke e os 22 testes PostgreSQL que
haviam ficado pendentes na primeira execução. Contrato SQL Java fixado em
`57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`, igual à CI existente.

[Evidência sanitizada](private-transport-tests.json). Container e rede Docker eram
exclusivos, porta publicada somente em 127.0.0.1, dados em tmpfs e credenciais
aleatórias temporárias. Cleanup confirmou remoção de container/rede, certificados,
senhas e temporários. Nenhum acesso ao RDS/AWS ocorreu nessa suíte; os testes de
smoke continuam simulando transporte SSM/HTTP. O PostgreSQL real comprova os
contratos SQL/TLS locais, não os dados de staging.

A PR corretiva #8 usa base `test/fase3-cpf-demo`; o workflow atual só executa em
PRs para main/push main. Ausência de checks nessa base não é CI aprovada. Exigir
Auth CI no SHA final ao integrar a correção em #6; a sequência documental continua
#6/correção antes de #7. Os commits posteriores ao SHA testado alteram apenas
relatos/evidências, sem mudança no código Python.
