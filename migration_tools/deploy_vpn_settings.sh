#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/vpn-settings-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$release_dir"
chmod 700 "$release_dir"
printf '%s\n' "$release_dir" > run/vpn-settings-release-path
python3 - "$release_dir" <<'PY'
import sqlite3,sys,os
for source_path,name in [('/opt/kingline-manager/data/manager.sqlite3','manager-before.db'),('/opt/kingline-staging/data/vpn_bot.db','business-before.db')]:
 with sqlite3.connect(source_path) as source:
  if name.startswith('manager'):
   assert source.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued')").fetchone()[0]==0,'active_installation'
  with sqlite3.connect(sys.argv[1]+'/'+name) as dest:source.backup(dest)
 os.chmod(sys.argv[1]+'/'+name,0o600)
PY
tar --exclude=__pycache__ -czf "$release_dir/site-code.tar.gz" site/src site/backend
tar --exclude=__pycache__ -czf "$release_dir/manager-code.tar.gz" -C /opt/kingline-manager/app node_manager
cp /opt/kingline-control/owner-profile.json "$release_dir/"
docker image tag kingline-staging:20261022 kingline-staging:before-vpn-settings-20261023
tar -xzf /opt/kingline-manager/manager-vpn-settings-20261023.tar.gz -C /opt/kingline-manager/app
systemctl restart kingline-node-manager kingline-site-admin
curl --silent --fail --retry 8 --retry-connrefused --retry-delay 1 http://127.0.0.1:8090/healthz
mv site/dist "$release_dir/dist"
tar -xzf staging-vpn-settings-20261023.tar.gz 2> "$release_dir/tar.log"
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/permissions-tests.log" 2>&1
cat "$release_dir/permissions-tests.log"
docker compose up -d --no-deps api
printf 'Release: %s\n' "$release_dir"
