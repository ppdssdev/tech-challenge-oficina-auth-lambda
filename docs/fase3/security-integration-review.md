# Integração documental e segurança — revisão de 2026-11-03

Adendo à [PR #7](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/pull/7).
Não substitui README, ADRs nem relatórios históricos. Esta revisão não executou
Invoke, abertura de concorrência, bootstrap, SQL no RDS ou deploy.

## Ordem e conflito editorial

Integrar primeiro a [PR #6](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/pull/6)
e sua correção de transporte/janela, depois atualizar #7 sobre o resultado revisado.
`git merge-tree --write-tree docs/security-integration-review origin/test/fase3-cpf-demo`
retornou conflito real em `README.md` nos heads `5ee81f4` e `6736f7f`.
Não copiar um README inteiro sobre o outro: preservar links/contratos de #7 e as
instruções operacionais de #6; repetir checks sobre o SHA resultante antes de merge.
Este trabalho prepara adendo separado e não resolve o README de outra frente.

Integrações relacionadas: [API #13](https://github.com/ppdssdev/tech-challenge-oficina/pull/13)
antes de [API #14](https://github.com/ppdssdev/tech-challenge-oficina/pull/14),
[K3s #6](https://github.com/ppdssdev/tech-challenge-oficina-infra-k3s/pull/6),
[RDS #8](https://github.com/ppdssdev/tech-challenge-oficina-infra-rds/pull/8).

## Bloqueios do smoke original

- `scripts/cpf_demo.py` em #6 só aceita origem Java HTTPS. O Service privado
  ClusterIP:8080 usa HTTP; SSM não adiciona HTTPS à aplicação. Não executar com
  HTTPS fictício, TLS desativado ou abertura do Gateway. A correção proposta
  restringe Java ao loopback literal/porta/rotas aprovadas sobre túnel SSM e
  port-forward, com opt-in explícito e testes de rejeição. TLS HTTPS do JWKS,
  assinatura RS256, issuer/audience/kid/tempos continuam obrigatórios.
- RDS privado exige rota aprovada. O adapter proposto usa endereço loopback
  somente para conexão (`hostaddr`), preservando hostname RDS e `verify-full`;
  não substituir `host` por localhost nem relaxar CA/hostname.
- Fixtures A/B/C/D não foram demonstradas. A view V2 deriva `active` de
  `auth.customer_access`, não de `public.customers.active`; allowlist e origem
  sintética também precisam ser comprovadas. Inventário/SQL somente leitura e
  plano mínimo estão no adendo RDS, ainda sem execução no staging.
- Java possui cadeias diferentes: `CustomerSecurityConfig` usa JWKS em CUSTOMER;
  `SecurityConfig` → `JwtAuthenticationFilter` → `CustomerJwtVerifier` usa PEM
  em ADMIN. Divergência de chave/configuração produz 401 em ADMIN. O aceite
  permanece **OS própria 200; sem token 401; CUSTOMER em ADMIN 403; OS alheia 404**.
  Não alterar Java, nem aceitar 401 no lugar de 403. Ver adendo API para arquivos.

O procedimento futuro executável e os comandos SSM estão no runbook da correção
sobre #6, `docs/fase3/cpf-demo-runbook.md`. Esse arquivo ainda não existe na base
original de #7; consultar a revisão de #6/correção até a integração, preservando
esta distinção. Nenhum comando `window`, `watchdog` ou `close` foi executado aqui.

## CI e operação são evidências diferentes

`.github/workflows/ci.yml` roda Auth contract/build em PRs para `main` e push em
`main`: testes, PostgreSQL descartável, Terraform mockado, build/smoke sem rede.
`JAVA_CONTRACT_SHA` continua `57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`;
CI verde não valida configuração nem comportamento Java do staging atual.
Artefato CI não assinado não equivale ao ZIP assinado implantado.

O smoke é automação manual do operador, fora da CI. Janela futura exige host de
recuperação independente, credenciais válidas, início dentro de 30 segundos do
watchdog armado, prazo principal de 180 segundos e leitura final de concorrência
zero em ambas as vias. A correção adiciona orçamento independente ao cleanup;
falha/timeout significa estado desconhecido até nova leitura, nunca sucesso.
Não existe autorização nesta revisão para abrir a Lambda.

## Roteiro preliminar de evidências

1. Registrar UTC, SHAs GitHub/checks, imagem/digest Java observado, Lambda
   versão/hash/assinatura, conta/região e estado fechado. Não registrar credenciais.
2. Inventário read-only privado: proveniência sintética e matriz de disponibilidade;
   não publicar CPF, UUIDs de clientes/OS, senha, JSON privado ou hashes de CPFs.
3. Registrar pré-requisitos SSM, pod/digest/porta, CA RDS e JWKS confiável. A porta
   local não atesta identidade sozinha; host/listener/túnel devem ser verificados.
4. Depois de autorização operacional própria: preflight, recuperação independente,
   janela limitada e somente linhas sanitizadas de PASS/FAIL/status. Invoke direto
   testa Lambda, não comprova autorização HTTP/SigV4 pelo Gateway fechado.
5. Exigir 200/401/403/404 exatos, encerramento confirmado por leitura zero e endpoint
   ainda desabilitado. Registrar bloqueio em vez de adaptar expectativa.

Os snapshots de GitHub e AWS anexos são observações datadas; os relatórios
anteriores continuam históricos. Não houve alteração em IAM, banco, Terraform,
Gateway, Lambda, Java ou acessos; OIDC ficou fora desta frente.

## Snapshot consultado e correção preparada

[Snapshot GitHub/AWS e acesso efetivo](https://github.com/ppdssdev/tech-challenge-oficina/blob/docs/fase3-final-audit/docs/fase3/security-review-evidence.md):
checks das bases consultadas aprovados; API write e outros três read para
`soat-architecture`, sem convites pendentes. Lambda zero, Gateway desabilitado
AWS_IAM, API 3/3 privada; nenhuma consulta SQL no RDS. Não confundir esses
checks anteriores com validação dos novos heads documentais.

[Lambda #8](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/pull/8)
contém a correção do adapter e prazo de cleanup, empilhada sobre #6. Sua base não
aciona o workflow atual; exigir CI no SHA final antes da integração em main.
O [runbook corretivo](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/blob/fix/private-cpf-review/docs/fase3/private-transport-review.md)
traz os comandos privados e limites. A janela continua não autorizada nesta frente.
