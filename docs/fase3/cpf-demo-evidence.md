# Evidências da preparação e matriz esperada

**Não é relatório de execução staging.** Conta/região/recursos foram informados
pelo operador na entrega original de #6, que não consultou estado real. Fontes e
SHAs estão no [runbook](cpf-demo-runbook.md). A correção #8 acrescentou leituras
AWS/kubectl sanitizadas, registradas no [snapshot datado](https://github.com/ppdssdev/tech-challenge-oficina/blob/docs/fase3-final-audit/docs/fase3/security-review-evidence.md),
e a contagem de allowlist no [adendo](private-transport-review.md). A matriz abaixo
continua pendente da execução completa dos gates; leituras isoladas não a aprovam.

## Complemento de revisão

Consultar a [revisão de transporte privado](private-transport-review.md): o
adapter loopback é opt-in e não prova HTTPS Java fim a fim. Os resultados originais
abaixo são históricos; a correção adiciona testes sem rede e prazo de cleanup.
Nenhuma execução staging ou fixture é inferida desses testes.

## Matriz para preencher após janela aprovada

| Caso | Evidência aceita | Situação nesta entrega |
| --- | --- | --- |
| Perfil/conta/região | perfil fixo oficina-staging, STS conta 797781632267, us-east-2 | Não executado |
| Lambda | Active/Successful, Python 3.13, versão e hash aprovados, assinatura + Enforce, VPC | Não executado |
| Concorrência inicial | valor explicitamente 0; ausência de reserva é falha | Não executado |
| Gateway | HTTP, POST /auth/token, AWS_IAM, AWS_PROXY v2, staging DEPLOYED, source ARN exato | Não executado |
| Endpoint | DisableExecuteApiEndpoint=true | Não executado |
| Reader/identidades | TLS verify-full, somente SELECT V2, A/B ativos, C inativo, D ausente | Não executado |
| Emissão A/B | Invoke síncrono 200 sem FunctionError; aplicação 200, RS256/JWKS verificados, TTL 900 | Não executado |
| OS própria A/B | GET CUSTOMER 200 e id de resposta igual ao solicitado | Não executado |
| Sem token/malformado/assinatura alterada | GET CUSTOMER 401 para cada caso | Não executado |
| B acessa OS A | 404 conforme contrato atual; **não afirmar 403** | Não executado |
| CUSTOMER → ADMIN | GET /api/v1/admin/work-orders **403 obrigatório**; 401/200 são FAIL | Não executado; divergência potencial identificada |
| D inexistente / C inativo | aplicação 401, somente AUTHENTICATION_FAILED, sem access_token | Não executado |
| CPF malformado | aplicação 400, somente INVALID_REQUEST, sem access_token | Não executado |
| Cleanup principal/independente | Put(0), Get=0 nas duas vias, inclusive após falha | Somente mock |
| HTTP Gateway/IAM ponta a ponta | chamada HTTP SigV4 real + controles + correlação | **Não demonstrado**; proposta separada |

Nenhum 5xx, timeout, redirect, status de transporte Lambda isolado ou simples
payload decodificado é aceito como aprovação. A assinatura JWT é verificada.
A existência das OS é confirmada pelos GETs positivos, evitando falso sucesso por
404 de recurso inexistente. Falta de fixture/status/allowlist é bloqueio; não seed.

## Evidência local desta PR

- 63 testes locais do smoke passaram; Ruff e `git diff --check` passaram.
- Primeira CI da PR, revisão `18a032028af8e908dce2642e85e9b7dd9d7d97da`: [Auth CI aprovada](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/actions/runs/41642644081), incluindo PostgreSQL descartável e auditoria de grants.
  A revisão posterior acrescenta a proteção contra abertura após prazo e seu teste;
  consultar o check da revisão final na PR.
- Testes do smoke: handler real, assinatura RSA efêmera, transports AWS/HTTP/SQL
  simulados e sockets/DNS proibidos. Cobrem sucesso, deriva de configuração,
  assinatura/claims, controles negativos, abertura ambígua, interrupções, cleanup
  com retry e ausência de confirmação, gates e saída sem identificadores.
- Ruff sobre código e testes. A CI existente roda o mesmo conjunto junto da suíte
  do projeto, PostgreSQL local descartável, Terraform mock e build/container.
- Os resultados finais e links de execução CI devem ser registrados na descrição
  da PR. Teste local/mock e CI verde **não atestam staging**, grants reais, cadeia
  JWKS implantada, validade do perfil ou recuperação real.

## Registro sanitizado da janela futura

Registrar data UTC, autorização operacional, responsável, revisão da ferramenta,
versão/hash do artefato assinado e SHA/image digest Java, início/fim, resultado de
cada check, status HTTP numérico, código de saída e confirmação independente de
concorrência zero. Os scripts emitem JSON Lines com nomes fixos de checks,
PASS/FAIL e status numérico quando aplicável; não exportam dumps AWS.

Não anexar CPF (nem seu hash, que permite enumeração), JWT, IDs de clientes/OS,
senha, chave, DSN, arquivo de entrada, Secrets Manager, corpo HTTP, logs SDK,
tracebacks/locals ou captura do terminal. Só publicar evidência revisada; manter
referências da autorização conforme o canal restrito da equipe.

Modelo de resultado final:

```text
Revisão da ferramenta: <SHA da PR>
Artefato assinado / versão: <hash aprovado> / <versão>
Deployment Java: <SHA ou digest>
Autorização/janela: <referência restrita>; <início UTC>–<fim UTC>
Checks: <PASS/FAIL por nome, status numéricos>
Concorrência final principal: <0 confirmado | desconhecido>
Concorrência final independente: <0 confirmado | desconhecido>
ADMIN: <403 PASS | outro status FAIL>
Gateway HTTP/IAM: NÃO DEMONSTRADO (somente configuração inspecionada)
Conclusão: <aprovado somente para fluxo direto + Java | reprovado/bloqueado>
```
