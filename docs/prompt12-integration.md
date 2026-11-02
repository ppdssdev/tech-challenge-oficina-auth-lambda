# Prompt 12 — fechamento da integração Lambda / Java / RDS

> Evidência histórica, anterior à reconciliação V11. Referências a V8/V1, hashes,
> contagens e resultados abaixo não autorizam staging. O contrato operacional atual
> exige V1–V11, leitura exclusiva de V2 e pin Java integrado no PR #7; consulte
> [o fechamento V2](fase3/lambda-v2-final.md) e [staging](infra-staging.md).

Revisão local em 2026-11-02. Correções preparadas; **não há aceite de provisionamento**.
Não foram executadas operações AWS, apply/destroy, criação de branch, commit ou PR.
Repositórios irmãos foram somente lidos; alterações preexistentes foram preservadas.

## Correções

- Preparação PostgreSQL consome V1–V8 e os três scripts reais do Java, na ordem
  bootstrap 01 → migrations como migrator → bootstrap reader como admin → grants 02.
  Exige confirmação de banco descartável, loopback, `oficina_db` e cluster vazio;
  recusa contrato ausente ou migrations futuras sem revisão. Não requer V10.
- Testes usam o LOGIN real `oficina_lambda`, elegibilidade em `auth.customer_access`
  e o comportamento V8 que exclui CPF ambíguo. Cobrem permissões sem depender do
  `default_transaction_read_only` do cliente, emissão sintética, TLS e negações.
- CI usa PostgreSQL `oficina_db` com TLS `verify-full`, certificado descartável fora
  do checkout e teste negativo de hostname/CA. Testes ignorados reprovam o job.
  O SHA antigo foi substituído pela referência obrigatória `JAVA_CONTRACT_SHA`.
- Terraform rejeita ARN de segredo com wildcard e confere a policy do endpoint
  contra o segredo e a role auth exatos; output `execution_role_arn` facilita o
  preenchimento da configuração K3s. Acrescentados cenários positivos/negativos de mocks.
- README e contrato staging foram atualizados: RDS já declara `oficina_db`; o K3s
  já implementa endpoint opcional, private DNS, SGs HTTPS e policy restrita.

## Compatibilidade inspecionada

| Item | Resultado e limite da evidência |
| --- | --- |
| K3s integration_contract v3 | Campos/tipos e consumo compatíveis; contrato preservado, endpoint em output separado |
| RDS | `oficina_db`, PostgreSQL privado/criptografado, 5432; output administrativo não é consumido pela Lambda |
| Rede auth | SG auth exclusivo da Lambda, saída 5432 e ingresso RDS por SG; HTTPS ao SG do endpoint quando habilitado |
| Endpoint opcional | Ausente mantém função fechada; necessário para janela sintética, sem NAT alternativo |
| IAM/Secrets Manager | GetSecretValue no ARN exato, SourceVpce no IAM e PrincipalArn da role exata na endpoint policy |
| SQL runtime | `oficina_lambda` → `oficina_auth_reader` NOLOGIN → SELECT somente na view; nenhum reuse de `oficina_admin` |
| TLS | Adapter fixa verify-full/CA/hostname RDS; parâmetros unitariamente verificados; handshake PostgreSQL ainda não executado aqui |
| JWT Java | Código CUSTOMER valida RS256/kid/iss/aud/UUID/role/TTL 900/iat=nbf, skew 30 s; públicas PEM por kid, HMAC administrativo separado |
| Exposição | Endpoint HTTP desabilitado, AWS_IAM, concorrência 0; janela 2 com UUIDs sintéticos e eligibility SQL explícita |

Detalhes de nomes/ARNs, conteúdo do segredo e sequência operacional estão em
[infra-staging.md](infra-staging.md). Nome sugerido para o segredo composto:
`oficina/staging/auth-runtime`; usar seu ARN real integral tanto na Lambda quanto na
policy K3s. Não confundir com `app-db-password`, `jwt-secret`, `admin-password` do node
ou com o segredo administrativo RDS. Nenhum nome sugerido representa recurso existente.

## Verificações desta execução

| Verificação | Resultado |
| --- | --- |
| Pytest completo, Python 3.12.3 disponível localmente | **69 passed, 16 skipped**, sem falhas nos casos executados; XML em `dist/tests-prompt12.xml` ignorado pelo Git |
| PostgreSQL real | **Não executado com sucesso**: tentativa de preparação com o Java real falhou na conexão local; nenhum SQL aplicado |
| Motivo dos 16 skips | Sem TEST_DATABASE_URL utilizável; Docker sem permissão, servidor PostgreSQL ausente e sockets TCP/Unix proibidos pela sandbox |
| Ruff | Aprovado para auth/scripts/tests |
| YAML/ShellCheck | YAML do CI parseado e todos os comandos shell aprovados |
| Build/ZIP | Dois builds offline com wheels/hashes do lock; SHA-256 idêntico; inspeção de layout, nativas e padrões de segredos aprovada |
| Smoke oficial Python 3.13 | **Bloqueado** por acesso negado ao Docker; build cross-platform não substitui execução no runtime |
| Fixture TLS | Certificado descartável validado por OpenSSL para localhost e 127.0.0.1; privada removida; isso não prova handshake libpq |
| Terraform fmt | Aprovado, recursivo |
| Terraform validate | **Bloqueado** ao iniciar provider: `listen unix ... setsockopt: operation not permitted` |
| Terraform test | **Bloqueado** antes dos cenários: 0 executados, sem evidência de aprovação dos 8 runs mock |
| Expressão da policy, Terraform console sem provider | 3 fixtures avaliadas: grant exato aceito, outra role e outro segredo rejeitados; não substitui validate/test |
| TFLint | **Bloqueado** na inicialização do plugin no mesmo ambiente restrito |
| Checkov Terraform 3.2.471 | **20 aprovados, 8 falhas, 0 ignorados, 0 erros de parsing** |
| Segredos | ZIP inspecionado; Checkov secrets sem achados; detect-secrets offline executado com arquivos não rastreados incluídos, **28 alertas** revisados como fixtures, referências, chave pública e checksums; nenhum segredo operacional identificado |
| Git diff --check | Aprovado |

ZIP desta verificação: identificador de build com 40 zeros, explicitamente sintético,
sem commit/release atribuídos ao working tree. SHA-256:
`2b1baa9f7bcc3dd543dffb7da042d8224e7524190eb3000563d27f5f67e87549`.
A chave RSA do runtime não foi alterada nem gerada como segredo operacional.

Os oito achados Checkov **permanecem visíveis**, sem novas supressões:

| Regra | Ocorrências | Situação atual |
| --- | --- | --- |
| CKV_AWS_338 | 2 | Logs retidos 7 dias; regra pede pelo menos um ano |
| CKV_AWS_158 | 2 | Log groups sem CMK própria |
| CKV_AWS_116 | 1 | Lambda sem DLQ; fluxo em revisão é invocação síncrona |
| CKV_AWS_272 | 1 | Sem Code Signing Config; checksum/build determinístico não equivalem a code signing |
| CKV_AWS_50 | 1 | X-Ray não habilitado |
| CKV_AWS_173 | 1 | Sem CMK própria para environment; environment contém referências/configuração, não secret values |

Esses achados exigem decisão de hardening/observabilidade conforme a política do projeto;
não demonstram incompatibilidade SQL/rede nem justificam afirmar que a varredura passou.
Não foram adicionados recursos pagos ou removidos controles para obter aprovação do scanner.
Auditoria de CVEs não foi executada: não há ferramenta/base de vulnerabilidades disponível
nesta validação offline. Não se conclui ausência de vulnerabilidades pelas versões fixadas.

## Bloqueios reais antes do futuro provisionamento/ativação

1. **Referência Java integrada — resolvido após PR #7:** pin definitivo
   `57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`, no arquivo raiz e na CI.
   [Contrato atual e validação em um comando](fase3/lambda-v2-final.md).
2. **Completar validação num runner permitido:** executar os 16 casos PostgreSQL/TLS,
   smoke Python 3.13, Terraform validate/test e TFLint. O CI foi preparado, mas não
   foi disparado nesta execução. Não liberar com base em skips ou resultados históricos.
3. **Preencher e conferir os insumos reais:** conta/outputs K3s-RDS, artefato revisado,
   backend/IAM de deployment e parâmetros JWT. Para ativação sintética, habilitar o
   endpoint opcional na stack K3s com ARN do segredo e role exatos; executar o bootstrap
   SQL, provisionar senha própria/chave/CA pelo canal seguro, distribuir pública ao
   Java e cadastrar somente fixtures sintéticas na tabela e na allowlist.
4. **Aceite no ambiente autorizado:** conferir grants do RDS gerenciado, IAM/policy,
   DNS, rede e TLS efetivos; invocação IAM controlada e autorização CUSTOMER/ADMIN/
   propriedade no Java. Resolver ou registrar a decisão sobre os oito achados Checkov.
   Manter concorrência zero e HTTP desabilitado até esse aceite.

Não são bloqueios atuais de código: renomear `oficina` no infra-rds, implementar
endpoint/SG HTTPS no K3s ou criar V10. Essas pendências anteriores foram removidas.
Abertura pública por CPF permanece fora do desenho: exigiria autenticação adicional.

## Identificação das fontes Java consultadas

São hashes de arquivos do working tree, não de commits publicados:

| Arquivo no Java | SHA-256 |
| --- | --- |
| `src/main/resources/db/migration/V8__create_customer_identity_view.sql` | `3e1ba21f5aa2fbadb1c367466823335169e00221b8704d1bf48ae96543e5718c` |
| `scripts/bootstrap-auth-reader.sql` | `da5bb436c6db784f0bdcb07f143c39b48d996af03fb8d991ee87fd783968512e` |
| `scripts/staging/01-bootstrap-roles.sql` | `95870c1de2f18f9f8e4ab307b121fca13afbc44479920b5f49c91e123b213d27` |
| `scripts/staging/02-runtime-grants.sql` | `5cea0002e639d586d736fd39456ee824b91327bd8d1a01eb42f2a18473e29d1b` |

Trabalho local encerrado para revisão; nenhuma execução de provisionamento autorizada
ou realizada por esta entrega.
