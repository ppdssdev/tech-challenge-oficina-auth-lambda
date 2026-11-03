# Preparação privada — 2026-11-03

A #6 incorporou #8 por fast-forward em `4058acf70221b3bdf49e017ec405bc2324dc5acd`.
[Auth CI 41691529210](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/actions/runs/41691529210)
aprovado nesse SHA. Nenhum merge em main. Esta #7 incorpora a branch funcional,
preserva os dois trechos do README e os relatos históricos.

## Evidência real e bloqueios

Leituras AWS/SSM de 18:11–18:15 UTC: concorrência 0; Gateway desabilitado;
RDS privado disponível; nó SSM Online; três pods Ready, Service ClusterIP.
ImageID observado: `sha256:40b333a1546a157438789945b6bc47d7eea5c59e8958ba9dd31176beb8686527`.
Allowlist: um ID distinto. Não foi feito SELECT no RDS: sem credencial reader/auditor
preconfigurada no operador; nó sem psql. Fixtures, ownership e proveniência seguem
**NÃO COMPROVADOS**. Nenhuma identidade foi inferida de contagens ou allowlist.

Somente leitura de permissões GitHub: `soat-architecture` tem write na API e read
na Lambda/K3s/RDS. Nenhuma permissão foi alterada.

## Patch isolado para ADMIN — não aplicado

[java-pem-trust.merge-patch.json](java-pem-trust.merge-patch.json) é um JSON merge
patch do ConfigMap `oficina-customer-jwt`, namespace `oficina-staging`.
[Snapshot sanitizado](java-trust-readonly-evidence.json): PEM desabilitado, zero
chaves; issuer/audience CUSTOMER iguais aos da Lambda; kid publicado no JWKS HTTPS,
RSA 3072 bits. A chave incluída é **somente pública**, obtida com TLS validado.

O patch altera apenas `data.customer-jwt.yml` e acrescenta
`data.lambda-current-public.pem`: enabled=true, mesmo issuer/audience/kid e caminho
`/config/jwt/lambda-current-public.pem`. O volume ConfigMap existente já monta esse
diretório. Mantém outros campos e protege contra configuração concorrente por
resourceVersion. Revalidar essa versão e a chave antes de qualquer aplicação futura;
não remover a precondição silenciosamente. Spring carrega a configuração no início:
um rollout futuro aprovado seria necessário. Nada foi aplicado ou reiniciado.

O código `CustomerJwtVerifier` rejeita quando desabilitado, produzindo 401 antes
do RBAC. Habilitar confiança PEM equivalente permite autenticar CUSTOMER; a regra
ADMIN continua exigindo ROLE_ADMIN e retorna 403. Algoritmos HS* do ADMIN continuam
no verificador existente, sem mudanças. Assinatura RS256, issuer, audience, kid,
role, UUID e tempos permanecem validados. Não há fallback que aceite token inválido.
A inferência da configuração observada não é um teste HTTP do pod nem emissão Lambda.

Coordenação sessão 2: nenhum arquivo Java/manifests da API foi editado. Entregar este
artefato isolado ao responsável; ainda não houve resposta identificando seu worktree.
Não aplicar simultaneamente uma segunda estratégia de confiança sem revisão conjunta.

## Primeiro bloco operacional — UBUNTU LOCAL, somente leitura

Do worktree documental atualizado, com o venv aprovado já existente:

```bash
cd /tmp/oficina-security-review/lambda-docs
source /tmp/lambda-v2-validation.bbK2ghVL/venv/bin/activate
bash scripts/private_inventory.sh
```

O script confere conta, Lambda zero, RDS privado e SSM; cria exclusivamente túnel
local 15432 e o encerra ao sair. Solicita caminho da CA RDS aprovada, opcionalmente
JSON privado, senha reader e papel/senha auditor já autorizado, sem eco de senhas.
Não busca Secrets, não faz grants nem usa administrador automaticamente. O listener
precisa pertencer à sessão criada; falha se a porta estiver ocupada. Atestar somente
em host controlado. TLS mantém host=DNS RDS, hostaddr=127.0.0.1 e verify-full.

Sem JSON, retorna contagens sanitizadas e FAIL de seleção incompleta: isso é
inventário parcial, não prova fixtures. Para verificar seleção, fornecer entrada
0600 do runbook com `provenance_ref` apontando ao registro privado aprovado,
`db_transport=ssm-loopback` e atestação. Não publicar esse arquivo ou seu hash.
O auditor verifica duas associações OS/cliente/veículo parametrizadas; o reader
verifica V2, estados e privilégios. A referência de proveniência exige revisão
humana: não prova por si só que dados são sintéticos. Não são impressos CPF/UUID,
senhas, tokens ou corpos de OS. Falta de privilégio interrompe, sem tentar escrita.

## Plano mínimo de dados/allowlist — somente revisão

Para emissão JWT + própria 200 + sem token 401 + ADMIN 403 + alheia 404: dois
clientes distintos bastam; um token A e uma OS de A e de B permitem comprovar
existência e ownership. A suíte preservada também emite B, valida sua OS com B e
testa inatividade separadamente: requer A/B ativos, C inativo e D ausente.
C não pode ser A/B no mesmo snapshot read-only: torná-lo inativo impediria emissão.
São três clientes por esses cenários, não por conveniência do script.

Delta condicionado ao inventário privado, sem executar:

| Item | Estado requerido | Alteração mínima candidata |
|---|---|---|
| A/B | CPF sintético canônico não ambíguo, V2 active=true | Reusar dados comprovados; inserir somente ausentes; duas linhas customer_access ativas se necessário |
| OS/veículo | Uma OS e veículo coerentes por A/B | Reusar; criar apenas faltantes, sem serviços/peças/outbox artificiais |
| C | Cliente distinto V2 active=false | Reusar inativo comprovado; ausência em customer_access já significa false, não exige INSERT nessa tabela |
| D | CPF sintético reservado, ausente de customers e V2 | Nenhuma inserção |
| Allowlist | A/B/C presentes | União da allowlist atual com IDs selecionados; preservar entradas; no melhor caso acrescentar dois IDs, somente se o ID existente for fixture comprovada |

Não é possível fixar valores/delta final sem SELECT e proveniência. Não substituir
allowlist inteira nem atualizar variáveis Lambda por mapa parcial. Revisão futura
precisa snapshot privado do antes, responsável, colisões/ambiguidades, RevisionId
Lambda e plano de reversão específico das linhas inseridas. Nunca revogar grants
ou apagar dados preexistentes para limpar demonstração. Alterar allowlist/configuração
ou dados continua fora da autorização desta entrega.

## Janela futura e recuperação independente

Continuam obrigatórios os gates do [runbook](cpf-demo-runbook.md): fixture/ownership,
confiança das duas cadeias e túneis atestados. Java via SSM entrega HTTP ao listener;
não é HTTPS ponta a ponta. JWKS e RDS conservam TLS validado.

Antes de abrir qualquer janela futura autorizada, outro host/operador com perfil
válido e permissão existente inicia `python -m scripts.cpf_demo watchdog --authorized`.
Esperar PASS armado; principal deve começar em até 30 s, com `window --authorized
--recovery-armed --input ARQUIVO_PRIVADO`. Prazo principal 180 s, cleanup separado
120 s; watchdog fecha em 240 s mesmo se o principal falhar. Não iniciar nova janela
até ambas as leituras finais zero. Alternativa independente imediata:
`python -m scripts.cpf_demo close --authorized`, sem depender de fixtures/JSON.
Não executar esses modos nesta preparação. Falha de rede/AWS em ambas as vias
impede garantia absoluta: estado desconhecido até GetConcurrency=0; nunca declarar
PASS apenas por Put(0). Concorrência zero não revoga JWT já emitido (TTL 900 s).

## Testes locais separados de runtime

Lambda: 92 testes smoke; suíte completa 200 passed, zero skips em Python 3.13.18 /
PostgreSQL 16.15 descartável TLS verify-full, cleanup confirmado. Ruff/diff-check
aprovados. Esses resultados pertencem ao código funcional #6, não ao RDS.
Inventário novo: validação sintática Bash/Python, ShellCheck e Ruff; SQL/SSM do
novo comando não executados. CI executa mocks/DB descartável, nunca staging.
Runtime: somente leituras AWS, ConfigMap/pods/Service via SSM e JWKS público HTTPS.
Nenhum Invoke, JWT operacional, GET autenticado Java, SQL RDS, abertura Lambda,
configuração aplicada, IAM/Terraform, merge ou deploy.

Testes Java executados na API `57536fa`: ReconciledSecurityTest (7/7),
CustomerJwtConfigurationTest (2/2) e api.CustomerJwtIntegrationTest (38 passes,
1 skip inicial por configuração do emissor Python). Asserções reais de filtro/PEM:
CUSTOMER autenticado → ADMIN 403; ADMIN HMAC compatível; assinatura, issuer,
audience adulterados → 401. Chaves efêmeras locais; nenhum JWT staging emitido.
Primeira tentativa restrita falhou por sandbox negar Docker; repetição com acesso
Docker local aprovado passou. Não confundir patch ConfigMap não aplicado com
execução do Java implantado.

Execução complementar final: **48 testes Java passaram, zero erros/falhas/skips**,
incluindo emissor Python real, com `-Dlambda.repo=/tmp/oficina-security-review/lambda`
e `-Dlambda.python=/tmp/lambda-v2-validation.bbK2ghVL/venv/bin/python`.
O teste `realPythonSignerAuthenticatesAsCustomer` usa o signer Lambda real com chave
local efêmera; continua sem emissão staging. Esta execução supera o skip inicial.
