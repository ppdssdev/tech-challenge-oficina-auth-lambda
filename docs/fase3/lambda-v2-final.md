# Fechamento Lambda V2 — 2026-11-02

Código e validação externa preparados; **publicação/deploy ainda sem aceite integral**.
O PR Java #7 já está integrado. HEAD, main e origin/main do clone Java local apontam
para `57eb5df2c3e32fe5663596cbecfcd43a5d3ed780`. Esse é o pin no arquivo
[`JAVA_CONTRACT_SHA`](../../JAVA_CONTRACT_SHA) e no workflow; não resta configurar
uma variável remota para escolher o contrato. O runner CI faz checkout desse SHA
com histórico completo. Localmente, o script somente lê refs e exporta o commit;
não faz fetch, checkout, alterações ou testes no repositório irmão.

## Comando único

Na raiz da Lambda:

```bash
bash scripts/validate_external.sh ../tech-challenge-oficina
```

Requisitos externos: Linux, Bash, Git, tar, Python **3.13** com venv/pip, Terraform
**1.14.6**, Docker local via socket Unix acessível e clone Java com o commit e
`origin/main` contendo-o. `PYTHON=/caminho/python3.13` seleciona o interpretador.
Precisa de acesso aos pacotes Python, registry Terraform/provider AWS 6.38.0,
imagem `postgres:16-alpine` e imagem Lambda fixada por digest no script, ou caches
locais previamente preparados. `PIP_NO_INDEX`/`PIP_FIND_LINKS` podem selecionar
wheels locais, incluindo wheels CPython 3.13 manylinux2014 x86_64 do lock de runtime.
O smoke força `linux/amd64`; hosts ARM precisam de emulação previamente disponível.

O mesmo comando roda na CI. Não há opção para aceitar skips, saltar etapas,
desligar TLS, usar SQL alternativo ou trocar o pin por uma branch. Se uma etapa
falhar, o processo retorna código não zero; o resumo marca PASS, FAIL e NÃO EXECUTADA
por etapa. Falhas de cleanup também reprovam a execução. Não interpretar sucesso
de etapas isoladas como aprovação integral.

Sequência implementada:

1. Varre fontes rastreadas/não ignoradas, rejeita padrões de segredos e V1 em `auth/`,
   cria snapshot do working tree e manifesto SHA-256 em diretório novo.
2. Confere SHA/ancestralidade e extrai Java imutável via `git archive`.
3. Confere Docker local e versões; instala dependências em venv privado.
4. Cria PostgreSQL 16 novo, dados em tmpfs, porta escolhida pelo Docker em
   `127.0.0.1`, senha aleatória e certificado efêmero; exige TLS `verify-full`.
5. Aplica bootstrap 01, V1–V11 como migrator, reader e grants 02 dos arquivos Java
   reais, usando `prepare_integration.py`. Exige banco vazio/roles novas.
6. Executa toda a suíte Python e verifica XML sem nenhum skip/xfail; executa Ruff.
7. Executa Terraform fmt, init sem backend, validate e exatamente 11 cenários mock
   com `command=plan`. Confere inventário e resumo JSON: 11 passes, zero falhas/skips.
8. Faz dois builds, compara checksum, inspeciona ZIP e executa smoke Python 3.13
   oficial sem rede. Finaliza com `git diff --check` no checkout original.

Cada execução usa `/tmp/lambda-v2-validation.<aleatório>`. Logs, XML, manifesto,
versões, imagem PostgreSQL efetivamente usada e ZIP ficam nesse diretório; o caminho
aparece no resumo. O tag PostgreSQL acompanha correções da versão 16, e seu image ID
é registrado; os wheels do ZIP usam hashes fixados. O nome do ZIP contém o HEAD
Lambda, mas o manifesto descreve as fontes reais do working tree, inclusive mudanças
ainda não commitadas. Isso não representa uma release limpa ou assinada.

## Revisão de contrato e segurança

V11 real: `customer_id uuid`, `cpf varchar(14)` limitado a 11 dígitos ASCII,
`active boolean` vindo de `coalesce(auth.customer_access.active, false)`.
Não há habilitação automática. Colisões com CPFs legados omitem a identidade;
`customers.active` permanece independente. A view tem `security_barrier=true`.

O bootstrap reader dá USAGE em `auth` e SELECT **somente** em V2. O LOGIN
`oficina_lambda` herda apenas `oficina_auth_reader`, sem atributos administrativos.
Não recebe acesso a V1/tabelas-base/escrita/TEMP/DDL/roles migrator ou API. A suíte
SQL testa privilégios efetivos, inclusive PUBLIC/herança/colunas, e negação de V1.
A confirmação dinâmica desses grants aguarda o runner externo.

`auth/postgres.py` usa exclusivamente `auth.customer_identity_v2`, query parametrizada,
`LIMIT 2` e transação de leitura. Inspeção do runtime e do ZIP não encontrou fallback
V1. Referências V1 restantes são documentação histórica, bloqueios do validador e
testes de acesso negado; a migration V10 real continua parte da sequência histórica.

RS256 foi comparado com `CustomerSecurityConfig.java` (JWKS) e
`CustomerJwtVerifier.java` (PEM) no SHA integrado: RSA >=2048, `alg=RS256`, `kid`
válido, `sub` UUID canônico, `role=CUSTOMER`, `iss`/`aud` configurados, `iat=nbf`,
`exp=iat+900`. A Lambda não emite CPF/roles/authorities. Esse formato satisfaz ambas
as cadeias. Java JWKS usa URI de confiança HTTPS, skew até 60 s/cache até 300 s;
PEM usa skew 30 s. A mesma chave pública/kid/issuer/audience precisa ser configurada
operacionalmente. A fixture JWKS pública não é uma chave operacional. Esta revisão
e o smoke de assinatura não substituem um teste de token real contra a API Java
com ownership e rotação na configuração de destino.

O script usa `set -Eeuo pipefail` somente no subprocesso, sem `eval` ou tracing de
credenciais. Limpa variáveis herdadas de AWS/PostgreSQL/Terraform/pytest, desabilita
metadata AWS e usa configuração Terraform vazia. Senhas/chaves são geradas fora
do checkout, com umask 077, e removidas com as fixtures pytest no EXIT/INT/TERM.
Só remove containers cujos IDs vieram dos seus próprios arquivos `--cidfile` novos;
não procura/remove containers por nome, não usa prune, não altera Kind ou volumes
preexistentes. Não há apply/destroy, assinatura AWS, commit, PR ou push. IAM,
code signing Enforce, endpoint fechado e allowlist obrigatória não foram alterados.
SIGKILL/quebra do host não permitem executar traps; nesse caso, os arquivos cid
privados identificam exclusivamente os recursos dessa execução para limpeza manual.

Scan das fontes rastreadas/não ignoradas e do ZIP: nenhum segredo operacional
identificado. O scanner recusa chaves privadas, access key IDs, JWK privado e arquivos
de credenciais; dez regressões verificam rejeição e preservação dos dados de origem.
Esse scan por padrões e a revisão não representam auditoria completa do histórico Git
ou de CVEs. Apenas fixtures sintéticas foram usadas; nenhum CPF real foi introduzido.

## Evidência deste sandbox

| Verificação | Resultado |
| --- | --- |
| Script integral | Pin/snapshot aprovados; bloqueou em `docker`, exit 1, resumo e cleanup executados |
| Docker | Acesso negado a `/var/run/docker.sock`; nenhum container criado; sem repetição |
| Pytest, Python 3.12.3 local | **86 passed, 21 skipped**; skips são exclusivamente PostgreSQL/TLS |
| Ruff, Bash syntax, Terraform fmt, diff check | Aprovados |
| Terraform validate | Bloqueado ao carregar schema AWS 6.38.0: plugin sem handshake/stdout; nenhuma repetição nem apply |
| Terraform 11 cenários | Não executados: provider indisponível no sandbox |
| Build/ZIP | Dois builds offline CPython 3.13 x86_64 idênticos; inspeção aprovada; cópia adulterada para V1 rejeitada e removida |
| Smoke Docker Python 3.13 | Não executado: mesmo bloqueio Docker; host não tem Python 3.13 |

XML: `/tmp/lambda-v2-final-tests-_513y1x_/tests.xml`.
Resumo da tentativa integral: `/tmp/lambda-v2-validation.woivD6iQ/summary.txt`.
ZIP desta execução em `/tmp/lambda-v2-build-z4o1ed5f/lambda/dist/`, checksum
`002c93e1108b52a1640404b49b1c1529522720f739c94fe20b625b569cf37940`.
Os relatórios anteriores em `dist/` foram preservados. O teste local em 3.12 não
substitui o requisito 3.13 do comando integral.

## Bloqueadores de publicação e deploy

- Executar o comando em runner permitido: todos os testes Python sem skips,
  PostgreSQL 16/TLS/grants reais, Terraform validate/11 cenários e smoke 3.13.
- Para publicação de release, obter execução verde sobre a revisão Lambda revisada
  e conferir manifesto/checksum do artefato. A CI modificada ainda não foi executada;
  eventuais checks obrigatórios da branch devem corresponder ao workflow atual.
- Para deploy, cumprir os insumos/aceites de [staging](../infra-staging.md): contratos
  reais de rede/RDS/conta, segredo/TLS/chaves/JWKS coerentes, endpoint privado exigido
  e autorização operacional. Validar CUSTOMER/ADMIN/ownership/rotação no Java.
- Assinar o ZIP por fluxo autorizado e perfil versionado aceito pelo Enforce,
  reinspecionar e executar smoke do ZIP final. O ZIP desta validação é **não assinado**.

## Arquivos deste fechamento

`JAVA_CONTRACT_SHA`, `.github/workflows/ci.yml`, `scripts/validate_external.sh`,
`scripts/validation_snapshot.py`, `tests/test_validation_snapshot.py`, `README.md`,
`docs/infra-staging.md`, `docs/fase3/lambda-v2-final.md`, `docs/fase3/lambda-v2-sync.md`,
e notas de estado/pin nos relatórios `docs/prompt04-delivery.md`,
`docs/prompt12-integration.md`, `docs/prompt13-security-integration.md`.
As demais alterações já presentes no working tree foram preservadas.
