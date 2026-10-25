#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/dark-permissions-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$release_dir"
chmod 700 "$release_dir"
printf '%s\n' "$release_dir" > run/dark-permissions-release-path
docker image tag kingline-staging:20261022 kingline-staging:before-dark-permissions-20261023
tar -czf "$release_dir/code.tar.gz" site/backend site/src
python3 - "$release_dir" <<'PY'
import os,sqlite3,sys
path=sys.argv[1]+'/business-before-permissions.db'
with sqlite3.connect('/opt/kingline-staging/data/vpn_bot.db') as source,sqlite3.connect(path) as dest:source.backup(dest)
os.chmod(path,0o600)
PY
mv site/dist "$release_dir/dist"
tar -xzf staging-dark-permissions-20261023.tar.gz 2> "$release_dir/tar.log"
docker compose build api > "$release_dir/build.log" 2>&1
if ! docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/permissions-tests.log" 2>&1; then
  tail -60 "$release_dir/permissions-tests.log"
  exit 1
fi
cat "$release_dir/permissions-tests.log"
docker compose up -d --no-deps api
printf 'Release: %s\n' "$release_dir"
