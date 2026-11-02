# Lambda auth — contrato staging para revisão

Atualizado na sincronização V2. [Contrato e validações atuais](fase3/lambda-v2-sync.md). [Resultados, classificação dos oito achados e bloqueios](prompt13-security-integration.md).
Nenhum recurso AWS foi provisionado. Os repositórios Java, infra-k3s e infra-rds foram
somente consultados; suas alterações locais preexistentes foram preservadas.

## Recursos e ativação

Root independente `infra/terraform`, Terraform 1.14.6 e provider AWS 6.38.0 com lockfile.
Backend local nesta preparação; antes do uso compartilhado, definir backend protegido
com bloqueio. Sem remote-state de irmãos ou secret values em inputs/outputs/state/ZIP.

Declara Lambda, configuração obrigatória de code signing, role/policy, dois log groups,
HTTP API, integração, rota, stage e permissão de invocação. Não cria VPC, subnet, SG, RDS, NAT, endpoint, Secret, KMS, EFS,
domínio, Function URL ou provisioned concurrency.

Lambda Python 3.13 x86_64, 256 MiB, timeout 10 s, concorrência reservada **zero**.
`synthetic_test_window=true` permite 2 somente com endpoint Secrets Manager informado
e lista não vazia de UUIDs sintéticos. A janela não abre HTTP: `disable_execute_api_endpoint`
é constante true, sem domínio/mapping ou variável de ativação pública.
A rota `POST /auth/token` exige `AWS_IAM`, payload 2.0, timeout 12 s, 1 req/s, burst 2.
Permissão de invocação restringe conta/API/stage/método/caminho.

Testes nessa janela usam invocação direta e operador com `lambda:InvokeFunction`
restrito ao ARN da função. O throttling HTTP não limita invocação direta. Conferir
permissões preexistentes e quota de concorrência da conta antes de habilitar a janela.
O gateway HTTP não é uma API privada; seu endpoint está desabilitado.

## Contratos dos irmãos

| Contrato | Estado no código consultado | Consumo Lambda |
| --- | --- | --- |
| K3s `integration_contract` | v3: schema_version, account_id, region, environment, vpc_id, private_subnet_ids, app_security_group_id, auth_security_group_id | Objeto completo; conta conferida separadamente, us-east-2/staging, duas subnets/AZs e SG auth distinto do app |
| RDS | `db_name="oficina_db"`, PostgreSQL privado e criptografado | Outputs rds_identifier, db_endpoint, db_port, db_name e db_security_group_id; nunca admin_secret_arn |
| SG auth | Egress 5432 às subnets privadas; RDS aceita ingress 5432 dos SGs app/auth | Lambda associa somente SG auth; não cria regra própria |
| Endpoint Secrets Manager | `enable_secretsmanager_endpoint=false` por padrão; quando habilitado, Interface em duas AZs, private DNS e regras 443 auth/app ↔ endpoint | Output separado `secretsmanager_endpoint_id` alimenta `secrets_endpoint_id`; não altera o contrato v3 |
| Java V11 / V2 | `auth.customer_access`, default false; view com barreira e exclusão de CPF ambíguo | SELECT parametrizado em `auth.customer_identity_v2`, LIMIT 2, sem acesso às tabelas-base |

O data source de endpoint confere VPC, serviço Ohio, Interface e private DNS; a
precondition também exige o grant `GetSecretValue` da policy K3s com ARN exato do
segredo e `aws:PrincipalArn` exato da role de execução. Os data sources de subnet/SG/RDS
verificam conta/VPC/AZ, banco/porta/endpoint e ausência de exposição pública declarada.
Ainda é necessário conferir os outputs e a rede efetiva no futuro ambiente autorizado;
validações locais não provam roteamento, DNS, IAM efetivo ou conectividade AWS.

O endpoint é opcional para provisionar a função fechada. Sem endpoint, a role não
recebe `GetSecretValue` e a janela sintética é rejeitada. Com endpoint aprovado no K3s,
o caminho privado já está implementado; não há pendência de desenvolver regras 443
ou renomear o banco RDS. Não usar NAT/egress internet como substituição silenciosa.

## Credenciais, nomes e IAM

A Lambda usa **um segredo de aplicação composto**, provisionado fora deste Terraform.
Nome sugerido: `oficina/staging/auth-runtime` (convenção, não recurso criado).
O ARN completo deve ser obtido após criação; seu sufixo não deve ser adivinhado.
`secret_arn` aceita o mesmo formato exato, sem wildcard, que o K3s.

| Consumidor | Referência / configuração | Conteúdo ou acesso |
| --- | --- | --- |
| Lambda | `secret_arn` → `AUTH_SECRET_ARN` | JSON com exatamente username, password, private_key_pem, ca_pem, kid |
| Endpoint K3s | `secretsmanager_endpoint_secret_arn` = mesmo ARN da Lambda | Allow `GetSecretValue` somente nesse segredo para a role auth |
| Endpoint K3s | `secretsmanager_endpoint_role_arn` | `arn:aws:iam::<conta>:role/oficina-staging-auth-execution`; também exposto como output `execution_role_arn` |
| Node K3s | `oficina/staging/app-db-password` | Senha runtime Java `oficina_api`; não compartilhar com Lambda |
| Node K3s | `oficina/staging/jwt-secret` | HMAC administrativo Java, separado da privada CUSTOMER RSA |
| Node K3s | `oficina/staging/admin-password` | Login administrativo da aplicação, distinto do administrador SQL RDS |
| Bootstrap RDS | Output `admin_secret_arn`, usuário `oficina_admin` | Exclusivo do bootstrap; não entra na Lambda, runtime API ou Job Flyway |

Os três nomes do node são os já declarados em `app-secrets-iam.tf`; a policy do
endpoint reutiliza essa lista, com condição na role node. Eles não concedem ao node
acesso ao segredo composto da Lambda, nem à Lambda acesso aos segredos Java.
Os padrões `-??????` do node representam somente o sufixo Secrets Manager; o grant
auth usa ARN completo exato.

No segredo Lambda, `username` deve ser **oficina_lambda**, `password` sua senha própria,
`private_key_pem` uma RSA >=2048 bits, `ca_pem` a CA RDS confiável e `kid` o identificador
público autorizado no Java. Runtime rejeita outro usuário, campos extras/vazios ou kid
divergente. ARN/env/Terraform não contêm a senha ou privada. Usar criptografia gerenciada
pelo Secrets Manager neste desenho; CMK própria exigiria key policy e `kms:Decrypt`
restrito, ainda ausentes.

IAM de execução: logs somente no log group da função, ENIs necessárias ao serviço
Lambda e `GetSecretValue` no ARN exato condicionado a `aws:SourceVpce`.
O Deny condicionado a `lambda:SourceFunctionArn` impede uso das ações ENI pelo código.
Sem RDS API, Secrets wildcard, Secrets write ou KMS wildcard. A policy do endpoint
complementa, mas não substitui, a policy de identidade.

## Bootstrap SQL e TLS

Ordem real do Java, em `oficina_db`:

1. Administrador bootstrap executa `scripts/staging/01-bootstrap-roles.sql`.
2. Provisiona senhas independentes de migrator/API/Lambda por canal seguro.
3. `oficina_migrator` aplica V1–V11 e fica proprietário dos objetos.
4. Administrador executa `scripts/bootstrap-auth-reader.sql`; migrator não possui CREATEROLE.
5. Administrador executa `scripts/staging/02-runtime-grants.sql`.

`oficina_lambda` LOGIN herda somente `oficina_auth_reader` NOLOGIN: USAGE em auth e
SELECT em `auth.customer_identity_v2`, além de CONNECT no banco. Sem acesso à V1,
sem associação a `auth_readonly` e sem acesso a `customers`, `app_users` ou
`customer_access`, sem escrita/DDL/TEMP ou membership no migrator/API. O bootstrap
recusa grants PUBLIC que ampliem esses privilégios. A senha administrativa não é runtime.

O adapter fixa host RDS Ohio, porta 5432, `oficina_db`, `oficina_lambda` e TLS
**verify-full**, com CA validada e arquivo temporário 0600. Não aceita hostaddr,
sslmode ou host vindos do segredo. Chave e DSN ficam em memória. Psycopg usa conexão
por invocação, timeout 2 s, statement_timeout 2 s e transação read-only, além dos grants.

GetSecretValue/AWSCURRENT usa boto3 do runtime, timeouts 1/2 s e uma tentativa.
Cache dura até 60 s; refresh descarta credenciais/CA antigas antes de buscar e falha
com 503 genérico, sem reutilizar segredo expirado. Ausência de configuração cloud em
Lambda falha fechado, sem fallback para chave de arquivo.

## Identidades e JWT

Habilitar exclusivamente fixtures sintéticas com `auth.customer_access.active=true`
e UUIDs correspondentes em `synthetic_customer_ids`. Nenhum cliente é habilitado
implicitamente pela V11; a API Java não tem grants para habilitá-lo. A allowlist não
prova que uma fixture é sintética: essa classificação deve ser conferida na preparação.
Zero/duas linhas, active=false, CPF ambíguo ou UUID fora da lista não emitem token.

`customers.active` controla o ciclo de negócio; não altera a habilitação V2.
Para suspender emissão, usar `auth.customer_access.active=false` ou remover a habilitação.

Os dois validadores Java aceitam o token Lambda RS256: `sub` UUID canônico,
`role=CUSTOMER`, `iss`, `aud`, `kid`, `iat=nbf`, TTL 900. O caminho por código usa
`app.jwt.customer` com PEM público por kid (skew 30 s); `/api/v1/customer/**` usa
`CUSTOMER_JWKS_URI`, `CUSTOMER_JWT_ISSUER`, `CUSTOMER_JWT_AUDIENCE` (skew até 60 s,
cache JWKS até 300 s). Configurar a mesma chave/issuer/audience nos dois validadores;
PEM não configura JWKS automaticamente. HMAC administrativo permanece separado.

Rotação requer distribuição prévia da pública nova nas duas configurações, rollout
PEM e consideração dos caches (segredo Lambda até 60 s e JWKS até 300 s), TTL 900 e
maior skew configurado antes de retirar a pública anterior.
Inativação SQL bloqueia novas emissões, sem revogação individual dos
JWTs emitidos. Mensagens uniformes não eliminam enumeração temporal. Abertura pública
exigiria fator adicional de autenticação e revisão específica; nenhum switch a permite.

Logs Lambda/Gateway retêm 7 dias, sem payload, IP, CPF, autorização ou token; access
logs contêm requestId/status/routeKey. Acompanhar erros, duração, throttles e conexões
na janela sintética. Endpoint continua sujeito a cobrança mesmo com concorrência zero.
Custos e dados reais da conta não foram consultados nesta entrega.


## Assinatura obrigatória do artefato (Prompt 13)

`aws_lambda_code_signing_config.auth` exige `Enforce`; não há modo Warn ou bypass.
`signing_profile_version_arns` deve conter 1–20 versões imutáveis, existentes e revisadas,
na conta autorizada/us-east-2, de perfis `AWSLambda-SHA384-ECDSA`. Este root não cria
perfil, bucket ou signing job e não concede Signer ao runtime. Somente declara a
configuração que será criada em um futuro apply autorizado.

O build determinístico gera ZIP não assinado. Ele deve passar pelo fluxo separado de
assinatura antes do deployment; reinspecionar e executar smoke do ZIP final sem
reempacotar. `artifact_path` e `artifact_sha256` referem-se ao arquivo assinado.
A Lambda verificará assinatura, publicador, integridade, expiração e revogação; sintaxe
de ARN e checksum locais não comprovam essas propriedades. Não foi feita assinatura AWS.
Os testes Terraform usam artefato/perfis fictícios, sem deployment ou aceite da assinatura.

Retenção 7 dias, criptografia padrão gerenciada e ausência de DLQ/X-Ray foram preservadas.
Sete achados Checkov continuam visíveis, com justificativas e pendências no relatório;
não há supressões nem aprovação automática de exceções.


## Pin Java e aceite V2

O contrato Java está fixado em `57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`,
main após PR #7, no arquivo `JAVA_CONTRACT_SHA` e na CI. A validação confere
ancestralidade em `origin/main` e extrai desse commit V1–V11 e bootstrap estrito.
Executar `bash scripts/validate_external.sh ../tech-challenge-oficina`; ver
[fechamento V2](fase3/lambda-v2-final.md). O ZIP gerado ainda não é release assinada.
Exigir testes PostgreSQL/TLS sem skips com `oficina_lambda`, negação da V1, smoke do
ZIP V2 em Python 3.13 e aceite CUSTOMER/ADMIN/ownership Java antes de staging.
Não ampliar grants para acomodar artefato antigo; atualizar o consumidor e preservar
V1 somente para consumidores legados com suas próprias roles.
