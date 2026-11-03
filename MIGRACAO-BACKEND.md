# Migração manual do backend

Configuração para Terraform 1.14.6. Nenhuma migração foi executada nesta alteração.
Providers, recursos e variáveis permanecem iguais.

Destino: `s3://oficina-staging-tfstate-797781632267/staging/lambda/terraform.tfstate`, região `us-east-2`, criptografia e lock S3 habilitados.

1. Suspenda execuções concorrentes. Use credenciais temporárias pelo ambiente ou perfil AWS, confirmando a conta `797781632267`. Não grave credenciais no código.
2. Confirme que o bucket já existe, tem versionamento habilitado e acesso restrito. A identidade precisa de `s3:ListBucket`, `s3:GetObject`/`s3:PutObject` no estado e `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject` no objeto de lock (mesma key com sufixo `.tflock`). Caso o bucket use uma chave KMS, confirme também as permissões correspondentes.
3. No checkout que contém o estado local autoritativo, faça backup seguro fora do Git do estado e de seu backup. Confirme o workspace `default` e que a key de destino não contém outro estado. Se houver estado remoto ou workspaces adicionais, interrompa e reconcilie antes de continuar.
4. Aplique os arquivos deste PR nesse checkout. No diretório `infra/terraform`, confira `terraform version` e `terraform fmt -check -recursive`.
5. Somente em execução manual autorizada, execute `terraform init -migrate-state` e revise a confirmação de cópia. Não use `-force-copy` nem `-reconfigure` para migrar o estado local.
6. Confira os recursos com `terraform state list` e revise `terraform plan` usando os mesmos parâmetros anteriores. Não aplique se houver alterações inesperadas. Preserve o backup até confirmar a migração e atualize os demais checkouts para usar o backend remoto.

Não versione `.terraform/`, estados, backups, planos, credenciais ou tfvars sensíveis. Esta alteração não cria o bucket e não executa init, apply, destroy, import, state push ou migração.
