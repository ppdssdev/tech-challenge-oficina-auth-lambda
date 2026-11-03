# Fixture B: proposta mínima, não executada

## Evidência disponível

O operador confirmou A em staging: V2 ativa, proveniência sintética, allowlist e
quatro OS próprias com veículos coerentes. Sua consulta retornou
`another_customer_has_order=false`. Este é resultado informado pelo operador;
esta preparação não repetiu SQL em staging.

Nos registros locais examinados, `lambda-fixture.env` e `os-demo-results.json`
identificam somente A. Não foi encontrada proveniência da segunda identidade ativa.
O script gerador existe, mas os scripts de recuperação selecionavam pelo marcador
sintético e data; isso não basta para atribuir a segunda identidade ao gerador.
Não escolher B só por estar ativa, por ter CPF válido ou pelo nome do cliente.

Antes de selecionar B existente, obter registro privado de criação contendo seu
UUID, origem sintética, responsável/data e referência ao processo gerador; conferir
esse UUID contra V2 e customers no RDS por SELECT parametrizado. Se não houver
registro inequívoco, não reutilizar a segunda identidade: planejar B novo sintético.
Nenhuma busca de dados de pessoas reais é necessária para esse passo.

## Delta mínimo

| Condição comprovada | INSERTs propostos |
|---|---|
| B existente, ativo, sintético, com veículo seu | 1 work_order |
| B existente, ativo, sintético, sem veículo | 1 vehicle + 1 work_order |
| Proveniência de B existente indisponível | 1 customer sintético + 1 customer_access ativa + 1 vehicle + 1 work_order |

A OS terá status RECEIVED, totais zero, sem peças, serviços, contatos, aprovação,
transições, notificações ou chamadas externas. É fixture técnica para GET; não
representa uma ordem criada pelo fluxo comercial. Não fabricar eventos históricos.
O schema permite esse estado e o smoke não exige histórico. Se o revisor exigir
criação pelo fluxo comercial, rever a proposta e os efeitos de outbox/histórico;
não misturar essa alternativa com a limpeza mínima abaixo.

## Procedimento exato para revisão

O [template parametrizado](review/fixture_b.py.review) contém `create`, `verify` e
`cleanup`, sem conexão, CLI ou execução automática. Não foi importado/executado;
somente sua sintaxe foi compilada. Não executar com Python otimizado (-O).

1. Preparar fora do Git um manifesto JSON 0600, diretório 0700, revisado pelo
   responsável. Campos: `a`, `a_order`, `b`, `vehicle`, `order`, `code`, `marker`,
   `new_customer`, `new_vehicle`, `provenance_ref`, `reviewed_synthetic=true`.
   Se criar cliente: também `cpf`; se criar veículo: também `plate`.
   Todos os UUIDs novos, código de OS, placa fictícia e marcador `SMOKE-B-...` devem
   ser reservados antes da transação; `marker` único de até 60 caracteres para
   caber também no model do veículo, código até 30 e placa até 7.
   Não copiar CPF de exemplos nem persistir senha/token nesse manifesto.
2. Revisor escolhe o menor ramo da tabela. Para reutilizar, os IDs de B/veículo
   vêm do inventário privado comprovado. Não atualizar nome, CPF, estado ou veículo
   preexistentes. Para B novo, CPF sintético gerado pelo procedimento aprovado,
   validado e sem colisão normalizada; a transação verifica colisão novamente.
3. Antes de qualquer execução futura, autorização separada para DML, janela sem
   concorrência de alterações nessas fixtures, Lambda zero e snapshot privado dos
   IDs preexistentes versus novos. Conferir migrations/constraints e triggers reais:
   qualquer trigger de negócio não revisada bloqueia essa proposta. Usar papel já
   autorizado para as tabelas do ramo escolhido; não criar papel/grant. O reader
   oficina_lambda não pode executar criação/limpeza. Não usar admin automaticamente.
4. Abrir conexão psycopg nova com autocommit=True, host=DNS RDS aprovado,
   hostaddr=127.0.0.1, port=15432, sslmode=verify-full e CA aprovada, sobre túnel
   atestado. Credencial por prompt oculto/mecanismo aprovado, nunca argv/log/env.
   Não iniciar outra transação antes das funções. Carregar manifesto privado como
   `p` e, **somente após autorização futura**, chamar `create(c, p)`.
   O template usa uma transação com timeouts; valida B ativo, vínculo do veículo e
   ownership final. Qualquer erro causa rollback total. Cada INSERT exige uma linha;
   não há ON CONFLICT nem atualização silenciosa. Não repetir criação em timeout
   ambíguo: executar verificação read-only pelos IDs reservados primeiro.
5. Depois do COMMIT confirmado, guardar no mesmo inventário privado o recibo de
   sucesso/UTC e flags dos registros efetivamente criados; nunca imprimir parâmetros
   ou exceção SQL que possa revelar valores. Sem recibo inequívoco, investigar por
   SELECT; não adivinhar quais registros remover.

## Verificação somente leitura

`verify(c, p)` é independente de `create`: inicia READ ONLY, confere TLS e retorna
somente `b_active_v2`, `own_a`, `own_b_with_vehicle`, `b_cannot_own_a`.
Todos devem ser true. A última verificação é SQL, não prova HTTP 404.

Como nenhum papel auditor dedicado foi encontrado, dividir as consultas entre
papéis existentes se necessário: oficina_lambda lê somente V2; oficina_api lê
work_orders/vehicles. Não presumir que oficina_api pode ler V2 ou conceder acesso.
As consultas exatas, parametrizadas, estão na função. Para checar se B já tem
veículo antes de criar, usar sessão READ ONLY autorizada com
`SELECT EXISTS(SELECT 1 FROM public.vehicles WHERE customer_id=%s)`; se true,
selecionar o ID apenas para o manifesto privado, sem imprimir em evidência pública.

## Limpeza futura, não executada

Após teste, Lambda zero confirmada pelas duas vias, janela encerrada e ausência de
uso concorrente: conectar como no passo 4 e chamar `cleanup(c, p)` usando o manifesto
original e recibo. A transação bloqueia a OS, confere IDs, marcador, status RECEIVED,
totais zero, timestamps sem alteração e ausência de itens/histórico/outbox/idempotência.
Só então exclui a OS; exclui veículo **somente se criado nesta operação**; exclui
customer_access e cliente **somente se B novo foi criado nesta operação** e não tem
outras OS/veículos. Não remove nada de A nem clientes/veículos reutilizados.

Qualquer dependência, mudança ou diferença de contagem gera rollback integral;
não usar CASCADE, não apagar eventos para forçar limpeza e não remover registro
apenas porque possui nome sintético. Repetição da limpeza já concluída falha fechada.
Confirmar ausência dos IDs novos por SELECT read-only depois do COMMIT.
Se uma futura alteração da allowlist incluir B, sua reversão é operação separada,
com snapshot/RevisionId próprios. Este template não altera allowlist/concorrência.

## Smoke preservado e bloqueios

O smoke atual em scripts/cpf_demo.py exige **token de B**:

- emite e verifica JWT para A e B;
- A consulta OS A → 200; B consulta OS B → 200;
- ausência/token inválido/assinatura alterada → 401;
- B consulta OS A → 404 (direção cruzada atual);
- A CUSTOMER consulta ADMIN → 403; 401 não é aceito como equivalente.

Logo, B ativo e na allowlist é requisito futuro, mesmo que somente adicionar uma
OS ao banco já permita observar outro proprietário. Não alteramos allowlist.
Adicionar B à allowlist futuramente exige preservar A e demais entradas, revisão
privada e autorização separada. Também permanece o cenário inativo: o preflight
atual exige C distinto, inativo e na allowlist, mais CPF D ausente. Criar B/OS não
resolve esses gates nem a aplicação futura do patch ADMIN. Nenhum cenário removido.

## Validação desta entrega

Inspeção do schema versionado e do smoke, compilação sintática do template e
`git diff --check`. Nenhuma criação, limpeza, SELECT staging, alteração de allowlist
ou concorrência foi executada. Dados reportados pelo operador estão separados das
verificações locais. O template requer revisão contra o schema efetivo antes de DML.
