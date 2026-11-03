# ADR 0001 — Lambda de autenticação sintética

**Status:** Código implementado; Lambda observada fechada; 2026-11-03. Registro consolidado a partir das fontes; não inventa data de
aprovação original nem transforma propostas em implantação.

## Contexto

Separar emissão CUSTOMER da API de negócio e demonstrar integração serverless com consulta mínima de identidade.

## Decisão

Lambda Python 3.13 consulta apenas V2 com oficina_lambda, exige UUID sintético habilitado e emite RS256 de 900s. Secrets Manager fornece segredo composto; endpoint HTTP desabilitado e concorrência padrão zero.

## Consequências e reavaliação

Escala/conexões devem ser limitadas; uma conexão por invocação, timeouts e reserved concurrency reduzem pressão no RDS. Cold start, rotação/cache e conectividade privada exigem operação. CPF não prova identidade; produção requer outro mecanismo de autenticação. Manter API administrativa HMAC isolada.

## Evidências

[adapter SQL](../../auth/postgres.py), [cloud](../../auth/cloud.py), [Terraform](../../infra/terraform/main.tf).
