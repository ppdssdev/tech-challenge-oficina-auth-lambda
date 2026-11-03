# Evidências consolidadas — Fase 3, Grupo 97

Pedro Paulo dos Santos Silva · Discord pedropaulo6447. Auditoria: 2026-11-03.
Baseline deste repositório: `1ff247717e35054967495ba7684742c67aaeb9da`. Alterações desta PR são somente documentais.
Não houve deploy, alteração funcional, Terraform apply/state nem pipeline.

A fonte central de status é a [entrega Fase 3](https://github.com/ppdssdev/tech-challenge-oficina/blob/main/docs/fase3/README.md), com
[matriz](https://github.com/ppdssdev/tech-challenge-oficina/blob/main/docs/fase3/evidence-matrix.md),
[coleta operacional](https://github.com/ppdssdev/tech-challenge-oficina/blob/main/docs/fase3/operational-evidence.md),
[diagramas](https://github.com/ppdssdev/tech-challenge-oficina/blob/main/docs/fase3/deployment-architecture.md)
e [RFCs/ADRs](https://github.com/ppdssdev/tech-challenge-oficina/blob/main/docs/fase3/decisions.md).
Links novos em main dependem do merge da PR documental da API; durante revisão,
consultar a branch da PR. Fontes de código anteriores permanecem fixadas por SHA.

| Evidência | Situação |
|---|---|
| Implementação | Código/main auditado, não só bootstrap |
| CI da baseline | Success conforme run abaixo; não equivale a deploy |
| Cloud | Consultas somente leitura e inventário SSM registrados centralmente |
| OIDC/CD | PR preparatória aberta; SCP impede auditoria provider; sem CD main |
| Produção | Não implantada/demonstrada nesta entrega |
| Evidências históricas | Resultados datados, não estado atual |

[CI auth](https://github.com/ppdssdev/tech-challenge-oficina-auth-lambda/actions/runs/41625965174).
Lambda observada Active/Python 3.13/256 MiB/10s, mas concorrência reservada zero;
Gateway HTTP com endpoint desabilitado e rota AWS_IAM. Não foi invocada.
O CodeSha256 implantado não foi vinculado ao SHA Git. Pendentes: assinatura real do
ZIP, grants/TLS reais e teste sintético E2E com ownership/negação/rotação.

Reprodução local: `bash scripts/validate_external.sh ../tech-challenge-oficina`.
Reprodução cloud somente leitura: `aws lambda get-function-concurrency
--function-name oficina-staging-auth` e `aws apigatewayv2 get-apis`, com perfil/região
explicitamente selecionados. Não habilitar concorrência para validar documentação.

Consulta complementar GitHub: PR/checks obrigatórios, zero aprovações humanas
exigidas; soat-architecture ainda não adicionado neste repo. Subject OIDC usa IDs
imutáveis e diverge da trust por nomes na PR K3s #5. Correções de IAM/governança
não fazem parte desta PR documental.
[Fontes e comandos](https://github.com/ppdssdev/tech-challenge-oficina/blob/main/docs/fase3/governance-audit.md).
