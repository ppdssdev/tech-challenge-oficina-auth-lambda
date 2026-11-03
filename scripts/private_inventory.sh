#!/usr/bin/env bash
# UBUNTU LOCAL: read-only inventory; never invokes or enables Lambda.
set -euo pipefail
set +x
umask 077
cd "$(dirname "$0")/.."
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
unset AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_ARN AWS_CONTAINER_CREDENTIALS_FULL_URI AWS_CONTAINER_CREDENTIALS_RELATIVE_URI
aws_stage() { aws --profile oficina-staging --region us-east-2 "$@"; }
for tool in aws session-manager-plugin python3 ss setsid; do command -v "$tool" >/dev/null; done
python3 -c 'import boto3, psycopg, jwt' 2>/dev/null || { echo 'FAIL: ativar venv com requirements-smoke.txt'; exit 1; }
test "$(aws_stage sts get-caller-identity --query Account --output text)" = 797781632267
test "$(aws_stage lambda get-function-concurrency --function-name oficina-staging-auth --query ReservedConcurrentExecutions --output text)" = 0
test "$(aws_stage rds describe-db-instances --db-instance-identifier oficina-staging-postgres --query 'DBInstances[0].PubliclyAccessible' --output text)" = False
test "$(aws_stage ssm describe-instance-information --filters Key=InstanceIds,Values=i-0ab373b1ba5c14b3e --query 'InstanceInformationList[0].PingStatus' --output text)" = Online
OFICINA_RDS_HOST=$(aws_stage rds describe-db-instances --db-instance-identifier oficina-staging-postgres --query 'DBInstances[0].Endpoint.Address' --output text)
read -r -p 'Caminho absoluto da CA RDS já aprovada: ' OFICINA_RDS_CA
test -f "$OFICINA_RDS_CA"
read -r -p 'JSON privado 0600 com proveniência/fixtures (Enter = somente contagens): ' OFICINA_INPUT
test -z "$(ss -H -ltn '( sport = :15432 )')" || { echo 'FAIL: porta 15432 já ocupada; não reutilizar listener desconhecido'; exit 1; }
OFICINA_TEMP=$(mktemp -d)
OFICINA_TUNNEL_PID=''
cleanup() {
  local result=$?
  trap - EXIT
  if [[ -n "$OFICINA_TUNNEL_PID" ]]; then
    kill -TERM -- "-$OFICINA_TUNNEL_PID" 2>/dev/null || true
    wait "$OFICINA_TUNNEL_PID" 2>/dev/null || true
  fi
  rm -rf -- "$OFICINA_TEMP"
  if [[ "$(aws_stage lambda get-function-concurrency --function-name oficina-staging-auth --query ReservedConcurrentExecutions --output text 2>/dev/null)" == 0 ]]; then
    echo 'PASS: concorrência Lambda continua zero (somente leitura)'
  else
    echo 'FAIL: estado final não confirmado; não abrir janela'
    result=1
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
OFICINA_PARAMS=$(python3 -c 'import json,sys; print(json.dumps({"host":[sys.argv[1]],"portNumber":["5432"],"localPortNumber":["15432"]}))' "$OFICINA_RDS_HOST")
setsid aws --profile oficina-staging --region us-east-2 ssm start-session \
  --target i-0ab373b1ba5c14b3e --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "$OFICINA_PARAMS" >"$OFICINA_TEMP/tunnel.log" 2>&1 &
OFICINA_TUNNEL_PID=$!
for _ in {1..30}; do
  kill -0 "$OFICINA_TUNNEL_PID" 2>/dev/null || { echo 'FAIL: sessão SSM indisponível'; exit 1; }
  [[ -n "$(ss -H -ltn '( sport = :15432 )')" ]] && break
  sleep 1
done
OFICINA_LISTENERS=$(ss -H -ltnp '( sport = :15432 )')
test -n "$OFICINA_LISTENERS"
printf '%s\n' "$OFICINA_LISTENERS"
printf '%s\n' "$OFICINA_LISTENERS" | python3 -c 'import sys; rows=[line.split() for line in sys.stdin]; assert rows and all(r[3]=="127.0.0.1:15432" and "session-manager" in " ".join(r[5:]) for r in rows)'
# The following interactive attestation must include the displayed process, AWS
# account/target and host ownership. A local socket alone does not prove identity.
OFICINA_ARGS=(--authorized --host "$OFICINA_RDS_HOST" --ca "$OFICINA_RDS_CA")
if [[ -n "$OFICINA_INPUT" ]]; then OFICINA_ARGS+=(--input "$OFICINA_INPUT"); fi
python3 -m scripts.cpf_inventory "${OFICINA_ARGS[@]}"
