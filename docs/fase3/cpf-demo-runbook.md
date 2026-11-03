# Demonstração CPF — Fase 3

**Entrega original de #6: preparação e testes locais/CI, sem chamadas AWS/RDS/Java
staging.** A revisão corretiva #8 acrescentou leituras sanitizadas AWS e kubectl
get via SSM, sem Invoke, SQL no RDS ou execução do smoke. O [adendo](private-transport-review.md)
registra o bloqueio atual da allowlist. Os comandos operacionais abaixo são para
uma janela futura, explicitamente autorizada. Não executar na CI.

## O que será demonstrado

1. Inspeção da configuração AWS, reader e identidades preexistentes.
2. Invocação **direta e síncrona da Lambda**, com evento HTTP API v2 simulado:
   CPF sintético → reader RDS → JWT CUSTOMER RS256.
3. HTTP GET real à API Java com esse JWT em memória; validação criptográfica local
   contra o JWKS confiável e validação pelo Spring na rota CUSTOMER.
4. Encerramento confirmado com reserved concurrency **0**.

A etapa 2 **não passa pelo API Gateway**, não testa SigV4/`execute-api:Invoke`,
roteamento HTTP, throttling nem logs de acesso do Gateway. Inspecionar a integração
não equivale a exercitá-la. `DisableExecuteApiEndpoint=true` permanece obrigatório;
não há opção de ativação nos scripts. A validação IAM HTTP está em proposta
separada, no fim deste documento.

## Contratos auditados e limites

- Lambda: `auth/request.py`, `auth/postgres.py`, `auth/cloud.py`, `auth/service.py`
  e `auth/jwt.py` em `1ff247717e35054967495ba7684742c67aaeb9da`.
- Java remoto: `469984555ea3b1d0d40580ada20144e2d01550b1`, consultado via GitHub.
  `GetWorkOrderService.getForCustomer` filtra proprietário; ausência e outro
  proprietário retornam **404**, não 403. O smoke exige 200 para cada uma das duas
  OS com seu dono antes de considerar a negação cruzada evidência de ownership.
- `CustomerSecurityConfig` usa JWKS na rota `/api/v1/customer/**`.
  `SecurityConfig`/`JwtAuthenticationFilter` usam outro validador, `CustomerJwtVerifier`
  com PEM, na cadeia ADMIN. O teste Java `CustomerJwtIntegrationTest` caracteriza
  **401** para token apenas confiável no JWKS; `ReconciledSecurityTest` caracteriza
  **403** quando o CUSTOMER é autenticado pela cadeia PEM.
- O requisito desta demonstração para `/api/v1/admin/work-orders` continua **403**.
  **401 é FAIL de contrato**, embora negue acesso; não aceitar silenciosamente
  401/403 como equivalentes. Não corrigir Java/configuração durante o smoke.
- `infra-k3s` em `10fa81e88e8e5cd9ab06fe8661cf1a735be26675`: rede/SG auth,
  egress privado RDS e endpoint Secrets Manager com Private DNS.
- `infra-rds` em `51bb0557156a42e0f6f71c05bc304a6e9af362bf`: RDS privado,
  `oficina_db:5432`, ingress por SG. Declarações Terraform não comprovam o estado
  real. As conexões futuras verificam TLS e o contrato, sem alterar rede.
- O pin `JAVA_CONTRACT_SHA` da CI existente é anterior à auditoria Java atual.
  Não foi alterado: CI existente prova seu contrato SQL fixado; o smoke futuro
  verifica o comportamento do deployment escolhido pelo operador.

Fontes Java:
[ownership](https://github.com/ppdssdev/tech-challenge-oficina/blob/469984555ea3b1d0d40580ada20144e2d01550b1/src/main/java/br/com/fiap/techchallenge/oficina/application/usecase/GetWorkOrderService.java),
[testes JWKS](https://github.com/ppdssdev/tech-challenge-oficina/blob/469984555ea3b1d0d40580ada20144e2d01550b1/src/test/java/br/com/fiap/techchallenge/oficina/integration/CustomerJwtIntegrationTest.java),
[filtro ADMIN](https://github.com/ppdssdev/tech-challenge-oficina/blob/469984555ea3b1d0d40580ada20144e2d01550b1/src/main/java/br/com/fiap/techchallenge/oficina/adapters/out/security/JwtAuthenticationFilter.java).

## Revisão do transporte privado

O listener Java versionado é HTTP ClusterIP:8080; o modo HTTPS original não é
executável sobre ele só por existir SSM. A [correção de transporte privado](private-transport-review.md)
descreve o adapter opt-in restrito a loopback, comandos SSM e seus limites, sem
relaxar JWKS/TLS RDS/RS256 ou o aceite ADMIN 403. Sem topologia atestada, bloquear.

## Pré-requisitos do operador — sem provisionamento automático

- Autorização registrada para conta `797781632267`, região `us-east-2`, perfil
  `oficina-staging`, função `oficina-staging-auth`, API `t6p562hkc9`, stage `staging`.
  Registrar responsável, horário UTC, revisão desta PR, versão/hash do ZIP
  **assinado implantado**, SHA/image digest Java e janela máxima de 180 segundos
  do processo principal. Não usar hash de um ZIP local não assinado.
- Congelar deploys, Terraform e outras janelas durante auditoria/demonstração.
  A concorrência é da função inteira, inclusive versões; não é um lock distribuído.
  Se o estado inicial não for zero, interromper e coordenar com o responsável.
- Linux/Python 3.13, ambiente privado e sem gravação de terminal/depuração HTTP.
  Instalar `requirements-smoke.txt` em venv fora do checkout. Não incluir boto3 no
  bundle Lambda. Não executar com `set -x`, debugger, `--showlocals`, APM ou logs SDK.
- Credenciais temporárias já configuradas no perfil aprovado, com duração que
  cubra a janela e recuperação; nenhum `credential_process` não revisado.
  O script fixa profile/region, consulta STS e recusa credenciais/endpoint overrides
  no ambiente. Não cria nem altera roles, policies ou configuração do perfil.
- Permissões já existentes: STS GetCallerIdentity; Lambda GetFunctionConfiguration,
  GetFunctionConcurrency, GetFunctionCodeSigningConfig, GetCodeSigningConfig,
  GetPolicy, InvokeFunction e PutFunctionConcurrency na função; API Gateway GET
  para API/routes/integration/stage/deployment. **Sem GetSecretValue** para o smoke.
  Permissões insuficientes são bloqueio, não motivo para expandir IAM automaticamente.
- Host com caminho aprovado para RDS privado e HTTPS Java/JWKS, ou adapter
  Java SSM-loopback explicitamente atestado conforme a revisão vinculada. Não abrir RDS,
  ingress, execute-api nem criar túnel implicitamente. A CA do RDS deve ser a
  confiável já aprovada; conexão usa hostname RDS, TLS `verify-full` e reader
  `oficina_lambda`. A senha é digitada em prompt oculto, nunca em argv/env/JSON.
- URL Java confiável (origem HTTPS e eventual prefixo), URL JWKS exata usada pelo
  Spring, issuer/audience/kid aprovados. O script não confia em `jku` do token,
  não segue redirects, não herda proxies e não permite TLS inseguro. Não usar
  `contracts/jwks.public.json` como se fosse a chave operacional.
- Inventário sintético já aprovado: dois clientes ativos A/B, uma OS existente de
  cada um, um cliente inativo C, um CPF válido sintético inexistente D. A/B/C devem
  constar na allowlist da Lambda; C precisa estar na view com `active=false`, para
  distinguir status de simples exclusão pela allowlist. O leitor confere A/B/C/D.
  `active` é da identidade V2 (`auth.customer_access`), independente de
  `customers.active`. CPF com dígitos verificadores válidos **não comprova origem
  sintética**: exigir proveniência do inventário. Não copiar exemplos da internet.
- Não gerar novos CPFs para staging, habilitar clientes, criar usuários, aplicar
  migrations, mudar grants nem editar allowlist. Fixture ausente/incompatível
  bloqueia a janela e volta ao responsável pelo preparo dos dados.
- Recuperação independente: outro operador/host com credenciais válidas e permissão
  preexistente para fechar a concorrência. Se isso não estiver disponível, **não abrir**.

## Entrada privada

Criar manualmente, fora do Git, JSON de propriedade do operador com modo **0600**
(diretório pai 0700). O script recusa symlink, arquivo compartilhado ou maior que
64 KiB. Os placeholders abaixo não são executáveis; substituí-los usando somente
inventário sintético aprovado. Não anexar esse arquivo, captura ou seu hash à PR.

```json
{
  "synthetic_only": true,
  "version": "$LATEST",
  "code_sha256": "BASE64_SHA256_DO_ZIP_ASSINADO_IMPLANTADO",
  "java_url": "https://ORIGEM_JAVA_APROVADA",
  "jwks_url": "https://ORIGEM_JWKS_APROVADA/CAMINHO",
  "issuer": "https://ISSUER_APROVADO",
  "audience": "AUDIENCE_APROVADA",
  "kid": "KID_APROVADO",
  "db_host": "HOST_APROVADO.us-east-2.rds.amazonaws.com",
  "db_ca_file": "/caminho/privado/ca-rds-confiavel.pem",
  "owner": {"cpf": "CPF_SINTETICO_A", "customer_id": "UUID_A", "order_id": "UUID_OS_A"},
  "other": {"cpf": "CPF_SINTETICO_B", "customer_id": "UUID_B", "order_id": "UUID_OS_B"},
  "inactive": {"cpf": "CPF_SINTETICO_C", "customer_id": "UUID_C"},
  "missing_cpf": "CPF_SINTETICO_D"
}
```

`version` deve corresponder ao destino da integração: `$LATEST` no Terraform
consultado. Alias não é aceito, para evitar resolução móvel. Versão numérica só
pode ser usada se a configuração real e sua permissão forem previamente revisadas;
o script atual exige policy não qualificada conforme o contrato auditado e falha
fechado em topologias diferentes.

## Validação permitida nesta entrega

Da raiz da Lambda, em venv de desenvolvimento já preparado:

```bash
python -m pytest -q tests/test_cpf_demo.py
python -m ruff check auth scripts tests
python -m scripts.cpf_demo --help
```

Os testes novos proíbem sockets/DNS e simulam AWS, HTTP e conexão SQL. Usam handler
real e RSA efêmero em memória. A CI existente executa toda a suíte, PostgreSQL
**local descartável**, Terraform **mock**, build e smoke em container sem rede;
não contém credenciais AWS/deploy. Os scripts antigos de preparação SQL são apenas
para o banco descartável da CI: **nunca executá-los no RDS**.

## Operação futura autorizada

Executar como módulo na raiz da Lambda, sem redirecionar stdin. O flag `--authorized`
não basta: também é necessário TTY e digitar a frase exibida, sem eco. `CI` definido
bloqueia todos os modos. Sem argumentos/consentimento, nenhum client AWS é criado.
Todos os comandos desta seção estão **não executados nesta entrega**.

1. Auditoria sem abrir janela/invocar Lambda:

   ```bash
   python -m scripts.cpf_demo preflight --authorized --input /caminho/privado/demo.json
   ```

   Confere STS, estado, runtime, versão/hash, VPC, referências de configuração,
   allowlist, assinatura e policy Enforce, concorrência zero, endpoint desabilitado,
   rota única IAM, integração v2, stage/deployment e resource policy com source ARN
   exato. No RDS consulta catálogos de privilégios efetivos (inclui PUBLIC/herança,
   colunas, schemas, sequências e funções de usuário), estado TLS/read-only e quatro
   SELECTs parametrizados com LIMIT 2. Não tenta escrita para testar negação.
   Privilégios adicionais bloqueiam a demonstração, sem revogar nada.

2. Imediatamente antes da janela, no **host independente** autorizado:

   ```bash
   python -m scripts.cpf_demo watchdog --authorized
   ```

   Após `watchdog_armed_240_seconds=PASS`, iniciar o processo principal em até
   **30 segundos**. Se houver atraso, não abrir; aguardar encerramento e rearmar.
   O watchdog fecha em 240 segundos e confirma zero; não abre a função e não é
   disparado pelo processo principal. Mantê-lo ativo até seu encerramento, mesmo
   que o principal termine cedo. Não sobrepor uma nova janela nesse intervalo.

3. No host com acesso privado ao banco/API:

   ```bash
   python -m scripts.cpf_demo window --authorized --recovery-armed --input /caminho/privado/demo.json
   ```

   Repetir a auditoria; digitar somente a senha do reader em prompt oculto. O prazo
   de 180 s inclui confirmação inicial, resolução das credenciais, preflight e
   esse prompt; não é renovado antes da abertura. Após os gates, reservar concorrência 2,
   confirmar 2 e executar cinco invocações síncronas: A, B, D, C e CPF malformado.
   O SDK não solicita log tail. O script verifica também `FunctionError`, tamanho,
   versão executada, status da aplicação e `Cache-Control: no-store`.
   JWTs não são persistidos nem impressos. A validação local verifica assinatura,
   RSA >=2048, kid, issuer, audience, UUID, role, claims exatos, tempos e TTL 900 s.
   HTTP Java verifica ambos donos, ausência, token malformado, assinatura alterada,
   ownership cruzado 404 e ADMIN 403. Não imprime corpos de OS/erros nem headers.

4. `finally` fecha a concorrência com `PutFunctionConcurrency(0)` e faz leitura
   de confirmação, mesmo após timeout ambíguo na abertura, erro de aplicação,
   SIGINT/SIGTERM/SIGHUP ou prazo excedido. Há três tentativas limitadas, sem retry
   automático de Invoke. Cada chamada SDK tem connect timeout 2 s/read timeout 15 s;
   HTTP 5 s e SQL connect/statement timeout 3 s. Falhas de casos HTTP são acumuladas
   para mostrar a matriz; falhas de dependência interrompem e executam cleanup.
   SIGINT/SIGTERM/SIGHUP são ignorados apenas durante cleanup. SIGALRM impõe
   orçamento separado de 120 s ao fechamento; expiração é FAIL e exige a via
   independente. Os 180 s do principal não garantem Get=0 nesse exato instante.

5. Só encerrar a demonstração após `final_concurrency_zero=PASS` **e** confirmação
   independente de zero pelo watchdog/operador. Exit 0 exige todos os casos passarem.
   A linha de cleanup PASS sozinha não significa sucesso da demonstração.
   Remover o arquivo privado conforme a política local; não alegar apagamento seguro
   de memória/disco. Os tokens já emitidos podem continuar válidos por até 900 s:
   concorrência zero impede novas emissões, **não revoga JWTs**.

## Recuperação e garantia de encerramento

Nenhum `finally`/trap local garante uma chamada AWS após SIGKILL, perda do host,
rede ou expiração/revogação de credenciais. O script **não declara encerramento
confirmado** nessas condições. A garantia operacional depende da segunda via,
do congelamento de mudanças e da leitura final de zero. Se a AWS estiver
inalcançável por ambas as vias, o estado é **DESCONHECIDO/BLOQUEADO**, nunca PASS;
escalar e persistir no fechamento quando o acesso voltar. Não abrir nova janela.

No host de recuperação, sem depender do JSON/CPF/JWT/senha:

```bash
python -m scripts.cpf_demo close --authorized
```

Esse modo valida perfil/conta e **somente** grava concorrência zero e a confirma.
Não usar `DeleteFunctionConcurrency`, pois removeria o limite. Não restaurar um
valor anterior positivo. Uma perda de resposta na abertura é tratada como
possível abertura bem-sucedida e sempre dispara fechamento. `close` pode ser
repetido com segurança pelo operador; não há alteração de IAM/Terraform/Gateway.
Uma falha genérica sai 1 sem traceback/valores e exige conferir recuperação.
Diagnóstico detalhado deve ocorrer em canal operacional restrito, nunca habilitando
logs de payload no smoke.

## Proposta separada: HTTP real com IAM no Gateway — NÃO EXECUTAR

O endpoint padrão desabilitado afeta todos os stages. Uma invocação SDK Lambda,
simulação de policy IAM, ou 403 no endpoint desabilitado **não prova** autorização
HTTP pela rota. [Documentação AWS do endpoint](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-disable-default-endpoint.html).

1. Submeter mudança própria com responsável, janela, custo, topologia, rollback e
   aprovação explícita. Manter `DisableExecuteApiEndpoint=true` e `AWS_IAM`.
   Inventariar eventual domínio customizado/mapping **já existente e aprovado**.
   Sem caminho aprovado, marcar teste HTTP **BLOQUEADO**, sem criar domínio/mapping.
   Domínio customizado por si só não torna uma HTTP API privada; se a exigência
   for isolamento de rede, a arquitetura precisa de revisão separada. Não propor
   resource policy de HTTP API como restrição: esse recurso não é suportado.
2. Revisar permissões existentes do principal permitido com recurso exato
   `arn:aws:execute-api:us-east-2:797781632267:t6p562hkc9/staging/POST/auth/token`.
   Um segundo principal já existente, sem essa permissão, serve de controle.
   Mudanças IAM, domínio, DNS, certificados ou mapping requerem PR/aprovação própria;
   este trabalho não as cria. [IAM/SigV4 para HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-access-control-iam.html).
3. Após aceite do caminho e da janela com recuperação independente, usar cliente
   SigV4 revisado, service `execute-api`, região `us-east-2`, credenciais temporárias
   em memória e corpo sintético privado. Não imprimir canonical request,
   Authorization, session token, CPF, request/response bodies ou JWT. Sem redirects.
4. Matriz proposta: assinatura válida/principal permitido → HTTP 200 e JWT verificado;
   sem assinatura → 403; assinatura inválida → 403; principal sem Invoke → 403.
   Confirmar pela correlação restrita de requestId/routeKey/status e métricas que
   apenas a chamada permitida atingiu a Lambda. Não interpretar um 403 genérico de
   endpoint fechado/mapping incorreto como prova positiva de IAM.
5. Usar o JWT permitido nos mesmos GETs Java e comparar os resultados. Fechar e
   confirmar concorrência zero por duas vias; confirmar endpoint ainda desabilitado
   e desfazer somente mudanças aprovadas na PR específica. Preservar apenas
   evidência sanitizada. Nenhum script executável de abertura HTTP acompanha esta PR.

[Referência AWS: concorrência reservada zero interrompe novas invocações](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html).
