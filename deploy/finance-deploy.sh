#!/bin/bash
# Installed by an administrator, root-owned. Never executes code downloaded from GitHub.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
revision=${1:-}
[[ $# == 1 && $revision =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid revision' >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'Root is required' >&2; exit 2; }
cd /opt/finance
exec 9>/var/lock/finance-deploy.lock
flock -w 600 9

# Actions supplies its short-lived, repository-scoped read token over encrypted stdin.
# Keep it out of process arguments, logs and the persistent application environment.
IFS= read -r github_token
github_token=${github_token%$'\r'}
# RFC 6750 bearer tokens can contain punctuation (including JWT separators).
[[ $github_token =~ ^[A-Za-z0-9._~+/-]+=*$ ]] || { echo 'Invalid GitHub bearer token format' >&2; exit 2; }
curl_config=$(mktemp /opt/finance/.github-read-token.XXXXXX)
trap 'rm -f "$curl_config"' EXIT
printf 'header = "Authorization: Bearer %s"\n' "$github_token" > "$curl_config"
unset github_token

latest_revision() {
    local response
    response=$(curl --config "$curl_config" --fail --silent --show-error --retry 3 --connect-timeout 10 --max-time 60 \
        -H 'Accept: application/vnd.github+json' \
        https://api.github.com/repos/jetcar/swedbank-spend-analyzer/commits/master) || return
    python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' <<< "$response"
}
current_revision=$(latest_revision)
if [[ $current_revision != "$revision" ]]; then
    echo "Skipping superseded revision $revision"
    exit 0
fi
if [[ -f REVISION && $(cat REVISION) == "$revision" ]]; then
    echo "Revision $revision is already deployed"
    exit 0
fi

stage="/opt/finance/deployments/$(date -u +%Y%m%dT%H%M%S)-$revision"
mkdir -p "$stage/deploy" "$stage/previous/deploy"
files=(docker-compose.server.yml deploy/nginx-server.conf deploy/nginx-egress.conf)
for file in "${files[@]}"; do
    cp -p "$file" "$stage/previous/$file"
    curl --config "$curl_config" --fail --silent --show-error --retry 3 --connect-timeout 10 --max-time 60 \
        -H 'Accept: application/vnd.github.raw+json' \
        "https://api.github.com/repos/jetcar/swedbank-spend-analyzer/contents/$file?ref=$revision" \
        -o "$stage/$file"
done
cp -p .env.server "$stage/previous/.env.server"

for component in app nginx postgres; do
    image="jetcar/bank-$component:sha-$revision"
    docker pull "$image"
    label=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
    [[ $label == "$revision" ]] || { echo "Unexpected revision in $image" >&2; exit 1; }
    docker image inspect --format '{{index .RepoDigests 0}}' "$image" > "$stage/$component.digest"
done

# Preserve active credentials and settings, remove obsolete static MCP secrets,
# and pin each pulled image by its registry digest.
python3 - "$stage" <<'PY'
from pathlib import Path
import sys
stage = Path(sys.argv[1])
updates = {f'{name.upper()}_IMAGE': (stage / f'{component}.digest').read_text().strip()
           for name, component in [('app', 'app'), ('nginx', 'nginx'), ('postgres', 'postgres')]}
for value in updates.values():
    assert value.startswith('jetcar/bank-') and '@sha256:' in value
lines = []
def parsed_assignment(line):
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or '=' not in stripped:
        return None
    key, raw = stripped.split('=', 1)
    return key.strip(), raw.strip()

for line in (stage / 'previous/.env.server').read_text().splitlines():
    assignment = parsed_assignment(line)
    key = assignment[0] if assignment else ''
    if key in {'MCP_CLIENT_TOKEN', 'PASSI_SERVICE_TOKEN', 'PASSI_SERVICE_OWNER_ID'}:
        continue
    if assignment and key in updates:
        line = f'{key}={updates.pop(key)}'
    lines.append(line)
lines += [f'{key}={value}' for key, value in updates.items()]
(stage / '.env.server').write_text('\n'.join(lines) + '\n')
PY

docker compose --project-directory /opt/finance --env-file "$stage/.env.server" \
    -f "$stage/docker-compose.server.yml" config --quiet
docker compose --project-directory /opt/finance --env-file "$stage/.env.server" \
    -f "$stage/docker-compose.server.yml" config --format json \
    | python3 -c 'import json,sys; e=json.load(sys.stdin)["services"]["mcp"]["environment"]; assert e["MCP_OAUTH_ENABLED"] == "1" and e["PASSI_ALLOWED_EMAILS"] and e["PASSI_RESOURCE"] == "https://finance.passi.cloud/mcp"'
current_revision=$(latest_revision)
if [[ $current_revision != "$revision" ]]; then
    echo "Skipping revision $revision: master changed while pulling images"
    exit 0
fi

compose() { docker compose --env-file .env.server -f docker-compose.server.yml "$@"; }
# Expand database variables inside the PostgreSQL container.
# shellcheck disable=SC2016
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$stage/database.dump"
test -s "$stage/database.dump"

changed=0
rollback() {
    result=$?
    trap - EXIT
    rm -f "$curl_config"
    if (( result != 0 && changed == 1 )); then
        echo 'Deployment failed; restoring previous images and configuration.' >&2
        install -m 600 "$stage/previous/.env.server" .env.server
        for file in "${files[@]}"; do
            install -m 644 "$stage/previous/$file" "$file"
        done
        if ! compose up -d --no-build --wait --wait-timeout 120; then
            echo 'ROLLBACK FAILED: manual intervention required.' >&2
        fi
        echo "Database backup retained at $stage/database.dump; database was not automatically overwritten." >&2
    fi
    exit "$result"
}
trap rollback EXIT
changed=1
install -m 600 "$stage/.env.server" .env.server
for file in "${files[@]}"; do
    install -m 644 "$stage/$file" "$file"
done
compose up -d --no-build --wait --wait-timeout 120

# Exercise the proxy, database, browser protection and private MCP after replacement.
health_response=$(curl --fail --silent --show-error --retry 5 --retry-delay 2 --retry-connrefused \
    --retry-all-errors --retry-max-time 60 \
    http://127.0.0.1:18000/health)
python3 -c 'import json,sys; assert json.load(sys.stdin)["database"] == "ok"' <<< "$health_response"
auth_response=$(curl --fail --silent --show-error --retry 5 --retry-delay 2 --retry-connrefused \
    --retry-all-errors --retry-max-time 60 \
    http://127.0.0.1:18000/auth/status)
python3 -c 'import json,sys; assert json.load(sys.stdin) == {"enabled": True, "authenticated": False}' <<< "$auth_response"
metadata_response=$(curl --fail --silent --show-error --retry 5 --retry-delay 2 --retry-connrefused \
    --retry-all-errors --retry-max-time 60 \
    http://127.0.0.1:18000/.well-known/oauth-protected-resource/mcp)
python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["resource"] == "https://finance.passi.cloud/mcp" and d["authorization_servers"] == ["https://passi.cloud/openidc"]' <<< "$metadata_response"
mcp_headers="$stage/mcp-unauthorized.headers"
status=
for _ in {1..30}; do
    if status=$(curl --silent --show-error --dump-header "$mcp_headers" --output /dev/null --write-out '%{http_code}' \
        -H 'Content-Type: application/json' -X POST --data '{}' http://127.0.0.1:18000/mcp); then
        [[ $status == 401 ]] && break
    fi
    sleep 2
done
[[ $status == 401 ]] || { echo "Expected OAuth challenge from MCP, got $status" >&2; exit 1; }
grep -Eqi 'www-authenticate: Bearer .*resource_metadata="https://finance\.passi\.cloud/\.well-known/oauth-protected-resource/mcp"' "$mcp_headers" \
    || { echo 'MCP OAuth challenge did not advertise protected-resource metadata' >&2; exit 1; }
printf '%s\n' "$revision" > REVISION
changed=0
echo "Deployed $revision from Docker Hub; health and MCP OAuth checks passed."
