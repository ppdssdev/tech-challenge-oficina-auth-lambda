#!/usr/bin/env bash
# Local compatibility bridge: AWS CLI resolves the existing approved login;
# boto3 receives process credentials through a pipe, never terminal output/files.
set -euo pipefail
set +x
umask 077
[[ $# -gt 0 ]]
export OFICINA_ORIGINAL_AWS_CONFIG="${OFICINA_ORIGINAL_AWS_CONFIG:-${AWS_CONFIG_FILE:-$HOME/.aws/config}}"
test -f "$OFICINA_ORIGINAL_AWS_CONFIG"
OFICINA_BRIDGE=$(mktemp -d /tmp/oficina-profile.XXXXXXXX)
trap 'rm -rf -- "$OFICINA_BRIDGE"' EXIT
cat > "$OFICINA_BRIDGE/credentials-helper" <<'HELPER'
#!/bin/sh
export AWS_CONFIG_FILE="$OFICINA_ORIGINAL_AWS_CONFIG"
exec aws --profile oficina-staging configure export-credentials --format process
HELPER
chmod 700 "$OFICINA_BRIDGE/credentials-helper"
printf '[profile oficina-staging]\nregion = us-east-2\ncredential_process = %s\n' \
  "$OFICINA_BRIDGE/credentials-helper" > "$OFICINA_BRIDGE/config"
export AWS_CONFIG_FILE="$OFICINA_BRIDGE/config"
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
unset AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_ARN AWS_CONTAINER_CREDENTIALS_FULL_URI AWS_CONTAINER_CREDENTIALS_RELATIVE_URI
"$@"
