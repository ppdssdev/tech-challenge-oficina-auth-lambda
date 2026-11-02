#!/usr/bin/env bash
# Run in a subprocess: bash scripts/validate_external.sh [path/to/java/repository]
# Never apply/destroy, reuse a database, or touch containers not created by this run.
set -Eeuo pipefail
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
JAVA_REPO=${1:-"$ROOT/../tech-challenge-oficina"}
PYTHON=${PYTHON:-python3}
WORK=$(mktemp -d /tmp/lambda-v2-validation.XXXXXXXX)
readonly ROOT WORK
stages=(sources java_contract docker prerequisites dependencies postgres python_tests ruff
        terraform_fmt terraform_init terraform_validate terraform_tests build zip smoke diff)
declare -A status=()
current=setup
POSTGRES_IMAGE=postgres:16-alpine
RUNTIME_IMAGE=public.ecr.aws/lambda/python@sha256:cf3e082b6c28721944dfef2c252c2e6df2f65aab3c2e2a64ce1f86f80c7aa13d
readonly POSTGRES_IMAGE RUNTIME_IMAGE

finish() {
    local result=$? id file cleanup_failed=0
    trap - EXIT INT TERM
    if ((result != 0)); then
        status[$current]=FAIL
        if [[ -f "$WORK/$current.log" ]]; then cat "$WORK/$current.log"; fi
    fi
    # IDs come exclusively from Docker's new --cidfile files in our private directory.
    for file in "$WORK/postgres.cid" "$WORK/smoke.cid"; do
        if [[ -s "$file" ]]; then
            id=$(cat "$file")
            if [[ "$id" =~ ^[0-9a-f]{64}$ ]]; then
                docker rm -f -v "$id" >>"$WORK/cleanup.log" 2>&1 || cleanup_failed=1
            else
                cleanup_failed=1
            fi
        fi
    done
    # All private credentials/keys (including pytest fixtures) stay in these fresh paths.
    rm -rf -- "$WORK/secrets" "$WORK/pytest-tmp" || cleanup_failed=1
    if ((cleanup_failed)); then result=1; fi
    {
        printf '\nRESUMO DA VALIDAÇÃO — Java %s\n' "${CONTRACT_SHA:-não verificado}"
        for stage in "${stages[@]}"; do printf '%-22s %s\n' "$stage" "${status[$stage]:-NÃO EXECUTADA}"; done
        printf '%-22s %s\n' cleanup "$([[ $cleanup_failed == 0 ]] && echo PASS || echo FAIL)"
        if ((result == 0)); then
            printf 'APROVADA: todas as etapas, zero skips; ZIP não assinado, sem autorização de deploy.\n'
        else
            printf 'REPROVADA: etapa %s (código %s); nenhuma liberação de publicação/deploy.\n' \
                "$([[ $cleanup_failed == 0 ]] && echo "$current" || echo cleanup)" "$result"
        fi
        printf 'Logs, manifesto, XML e artefatos isolados: %s\n' "$WORK"
    } | tee "$WORK/summary.txt"
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_stage() {
    current=$1
    shift
    printf '\n>>> %s\n' "$current"
    "$@" >"$WORK/$current.log" 2>&1
    status[$current]=PASS
    cat "$WORK/$current.log"
}

java_contract() {
    CONTRACT_SHA=$(cat "$WORK/lambda/JAVA_CONTRACT_SHA")
    [[ "$CONTRACT_SHA" =~ ^[0-9a-f]{40}$ ]]
    if [[ -n ${JAVA_CONTRACT_SHA:-} && "$JAVA_CONTRACT_SHA" != "$CONTRACT_SHA" ]]; then
        echo 'JAVA_CONTRACT_SHA differs from the reviewed pin'; return 1
    fi
    git -C "$JAVA_REPO" cat-file -e "$CONTRACT_SHA^{commit}"
    # Read local refs only: CI fetches origin/main; never fetch/write into a sibling.
    git -C "$JAVA_REPO" merge-base --is-ancestor "$CONTRACT_SHA" origin/main
    mkdir "$WORK/java"
    git -C "$JAVA_REPO" archive "$CONTRACT_SHA" | tar -x -C "$WORK/java"
    test -f "$WORK/java/src/main/resources/db/migration/V11__create_explicit_customer_identity_view.sql"
    echo "Java contract extracted from integrated commit $CONTRACT_SHA"
}

prerequisites() {
    "$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 13), "Python 3.13 required (set PYTHON)"'
    command -v terraform
    terraform version -json >"$WORK/terraform-version.json"
    "$PYTHON" -c 'import json,sys; assert json.load(open(sys.argv[1]))["terraform_version"] == "1.14.6"' \
        "$WORK/terraform-version.json"
}

docker_available() {
    local endpoint
    if [[ -n ${DOCKER_CONTEXT:-} || -z ${DOCKER_HOST:-} ]]; then
        endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
    else
        endpoint=$DOCKER_HOST
    fi
    [[ "$endpoint" == unix://* ]] || { echo 'A local Unix-socket Docker daemon is required'; return 1; }
    docker info --format '{{.ServerVersion}}'
}

dependencies() {
    "$PYTHON" -m venv "$WORK/venv"
    "$WORK/venv/bin/python" -m pip install --disable-pip-version-check -r requirements-dev.txt
    "$WORK/venv/bin/python" -m pip freeze >"$WORK/python-packages.txt"
}

postgres() {
    mkdir "$WORK/secrets"
    "$PY" scripts/prepare_test_tls.py "$WORK/secrets/tls"
    "$PY" -c 'import secrets,sys; from pathlib import Path; Path(sys.argv[1]).write_text(secrets.token_hex(32))' \
        "$WORK/secrets/postgres-password"
    # No bind mount of data, no named volume, no fixed name or host port.
    docker create --cidfile "$WORK/postgres.cid" --label "oficina.validation=$WORK" \
        --publish 127.0.0.1::5432 --tmpfs /var/lib/postgresql/data:rw,nosuid,nodev \
        -e POSTGRES_DB=oficina_db -e POSTGRES_PASSWORD_FILE=/tmp/validation-password \
        --entrypoint /bin/sh "$POSTGRES_IMAGE" -c \
        'set -eu; chown postgres:postgres /tmp/server.key /tmp/server.crt /tmp/validation-password; chmod 600 /tmp/server.key; exec docker-entrypoint.sh "$@"' \
        sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key
    local id port
    id=$(cat "$WORK/postgres.cid")
    docker cp "$WORK/secrets/postgres-password" "$id:/tmp/validation-password"
    docker cp "$WORK/secrets/tls/server.key" "$id:/tmp/server.key"
    docker cp "$WORK/secrets/tls/server.crt" "$id:/tmp/server.crt"
    docker start "$id"
    docker inspect --format '{{.Image}}' "$id" >"$WORK/postgres-image-id.txt"
    test "$(docker inspect --format '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostIp}}' "$id")" = 127.0.0.1
    port=$(docker inspect --format '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}' "$id")
    [[ "$port" =~ ^[0-9]+$ ]]
    export PGSSLROOTCERT="$WORK/secrets/tls/server.crt"
    TEST_DATABASE_URL="host=127.0.0.1 port=$port dbname=oficina_db user=postgres password=$(cat "$WORK/secrets/postgres-password") sslmode=verify-full"
    export TEST_DATABASE_URL
    "$PY" - <<'PY'
import os
import time
import psycopg
for attempt in range(30):
    try:
        with psycopg.connect(os.environ['TEST_DATABASE_URL'], connect_timeout=2) as connection:
            assert connection.execute('SHOW server_version_num').fetchone()[0].startswith('16')
            assert connection.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone() == (True,)
        break
    except psycopg.OperationalError:
        if attempt == 29:
            raise
        time.sleep(1)
print('PostgreSQL 16 ready with verified TLS')
PY
    "$PY" scripts/prepare_integration.py "$WORK/java" --disposable
}

python_tests() {
    "$PY" -m pytest -q -ra --basetemp="$WORK/pytest-tmp" --junitxml=dist/tests.xml
    "$PY" - <<'PY'
from xml.etree import ElementTree
root = ElementTree.parse('dist/tests.xml').getroot()
assert root.findall('.//testcase'), 'No Python tests executed'
assert not root.findall('.//skipped'), 'Any skip (including xfail) blocks validation'
print('All Python tests executed: zero skips')
PY
}

terraform_tests() {
    "$PY" scripts/prepare_terraform_fixture.py
    "$PY" - <<'PY'
from pathlib import Path
import hcl2
from hcl2.utils import SerializationOptions
root = Path('infra/terraform')
files = list(root.glob('*.tftest.*')) + list((root / 'tests').glob('*.tftest.*'))
assert files == [root / 'tests/closed.tftest.hcl'], 'Review changes to Terraform test inventory'
data = hcl2.loads(files[0].read_text(), serialization_options=SerializationOptions(strip_string_quotes=True))
mock = data.get('mock_provider', [])
assert len(mock) == 1 and set(mock[0]) == {'aws'} and 'alias' not in mock[0]['aws']
assert not data.get('provider'), 'Only mock providers are allowed'
assert len(data['run']) == 11, 'Expected exactly 11 reviewed scenarios'
for run in data['run']:
    body = next(iter(run.values()))
    assert body['command'] == 'plan', 'Only mock plans allowed'
    assert 'module' not in body and 'providers' not in body
PY
    terraform -chdir=infra/terraform test -no-color -json >"$WORK/terraform-tests.jsonl" || {
        cat "$WORK/terraform-tests.jsonl"; return 1;
    }
    "$PY" - "$WORK/terraform-tests.jsonl" <<'PY'
import json
import sys
events = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
summary = [event['test_summary'] for event in events if event.get('type') == 'test_summary']
assert len(summary) == 1, 'Missing Terraform test summary'
assert summary[0]['passed'] == 11 and summary[0]['failed'] == 0 and summary[0]['errored'] == 0, summary
assert summary[0].get('skipped', 0) == 0, summary
print('Terraform: 11 passed, zero failed/errored/skipped')
PY
}

build() {
    "$PY" scripts/build.py "$LAMBDA_SHA"
    cp "dist/$ARTIFACT.sha256" "$WORK/first-build.sha256"
    "$PY" scripts/build.py "$LAMBDA_SHA"
    cmp "$WORK/first-build.sha256" "dist/$ARTIFACT.sha256"
    echo 'Two builds produced identical ZIP bytes'
}

smoke() {
    "$PY" -m zipfile -e "dist/$ARTIFACT" "$WORK/bundle"
    # These are public release inputs. Allow reads across Docker user namespaces
    # without DAC capabilities; the enclosing host WORK directory stays private.
    chmod -R a+rX "$WORK/bundle"
    chmod a+r "$PWD/scripts/smoke_bundle.py"
    # Runtime is pinned; amd64 matches the native wheels. No network or AWS credentials.
    docker create --cidfile "$WORK/smoke.cid" --label "oficina.validation=$WORK" \
        --platform linux/amd64 --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges --entrypoint python \
        --mount "type=bind,source=$WORK/bundle,target=/var/task,readonly" \
        --mount "type=bind,source=$PWD/scripts/smoke_bundle.py,target=/tmp/smoke.py,readonly" \
        -e PYTHONPATH=/var/task -e PYTHONDONTWRITEBYTECODE=1 "$RUNTIME_IMAGE" /tmp/smoke.py
    local id
    id=$(cat "$WORK/smoke.cid")
    docker start -a "$id"
    test "$(docker inspect --format '{{.State.ExitCode}}' "$id")" = 0
}

# Prevent inherited DSNs, AWS credentials, pytest flags or Terraform injection from
# redirecting this local validation. Preserve only pip package-source settings.
while IFS= read -r name; do
    case "$name" in
        PYTHON|PIP_NO_INDEX|PIP_FIND_LINKS|PIP_INDEX_URL|PIP_EXTRA_INDEX_URL) ;;
        AWS_*|TF_*|PG*|TEST_DATABASE_URL|DATABASE_URL|PYTEST_*|PYTHON*|PIP_*|APP_ENV|JWT_*|AUTH_*|DB_*)
            unset "$name" ;;
    esac
done < <(compgen -e)
export AWS_EC2_METADATA_DISABLED=true TF_IN_AUTOMATION=1 CHECKPOINT_DISABLE=1
export PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PIP_CONFIG_FILE=/dev/null PIP_NO_CACHE_DIR=1
export AWS_CONFIG_FILE="$WORK/no-aws-config" AWS_SHARED_CREDENTIALS_FILE="$WORK/no-aws-credentials"
# Empty CLI config prevents implicit cloud/backend/provider overrides from ~/.terraformrc.
touch "$WORK/terraform.rc"
export TF_CLI_CONFIG_FILE="$WORK/terraform.rc"

run_stage sources "$PYTHON" "$ROOT/scripts/validation_snapshot.py" "$ROOT" "$WORK/lambda"
run_stage java_contract java_contract
run_stage docker docker_available
run_stage prerequisites prerequisites
cd "$WORK/lambda"
mkdir dist
LAMBDA_SHA=$(git -C "$ROOT" rev-parse HEAD)
ARTIFACT="auth-lambda-python313-x86_64-$LAMBDA_SHA.zip"
printf '%s\n' "$LAMBDA_SHA" >"$WORK/lambda-head.txt"
git -C "$ROOT" status --short >"$WORK/lambda-status.txt"
run_stage dependencies dependencies
PY="$WORK/venv/bin/python"
run_stage postgres postgres
run_stage python_tests python_tests
run_stage ruff "$PY" -m ruff check auth scripts tests
run_stage terraform_fmt terraform -chdir=infra/terraform fmt -check -recursive
run_stage terraform_init terraform -chdir=infra/terraform init -backend=false -input=false -no-color
run_stage terraform_validate terraform -chdir=infra/terraform validate -no-color
run_stage terraform_tests terraform_tests
run_stage build build
run_stage zip "$PY" scripts/validate_zip.py "dist/$ARTIFACT"
run_stage smoke smoke
run_stage diff git -C "$ROOT" diff --check
