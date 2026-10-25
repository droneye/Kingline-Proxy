#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/node-network-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$release_dir"
chmod 700 "$release_dir"
printf '%s\n' "$release_dir" > run/node-network-release-path
python3 - "$release_dir" <<'PY'
import sqlite3,sys,os
with sqlite3.connect('/opt/kingline-manager/data/manager.sqlite3') as source:
 assert source.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued')").fetchone()[0]==0,'active_installation'
 assert source.execute("SELECT count(*) FROM vpn_changes WHERE state IN ('queued_check','checking','queued_apply','applying','rollback_failed')").fetchone()[0]==0,'active_configuration'
 with sqlite3.connect(sys.argv[1]+'/manager-before.db') as dest:source.backup(dest)
os.chmod(sys.argv[1]+'/manager-before.db',0o600)
PY
tar --exclude=__pycache__ -czf "$release_dir/site-code.tar.gz" site/src site/backend
tar --exclude=__pycache__ -czf "$release_dir/manager-code.tar.gz" -C /opt/kingline-manager/app node_manager
cp /opt/kingline-control/owner-profile.json /opt/kingline-control/nodes.json /opt/kingline-control/smart_dns.py /opt/kingline-control/tunnel_health_policy.py "$release_dir/"
docker image tag kingline-staging:20261022 kingline-staging:before-node-network-20261023
systemctl stop kingline-node-manager kingline-site-admin
tar -xzf /opt/kingline-manager/manager-node-network-20261023.tar.gz -C /opt/kingline-manager/app
cp /opt/kingline-control/smart_dns.node-network.py /opt/kingline-control/smart_dns.py
cp /opt/kingline-control/tunnel_health_policy.node-network.py /opt/kingline-control/tunnel_health_policy.py
systemctl restart kingline-node-manager kingline-site-admin kingline-smart-dns
curl --silent --fail --retry 8 --retry-connrefused --retry-delay 1 http://127.0.0.1:8090/healthz
mv site/dist "$release_dir/dist"
tar -xzf staging-node-network-20261023.tar.gz 2> "$release_dir/tar.log"
rm -f site/src/AdminVPN.jsx site/src/vpn-settings.css
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/permissions-tests.log" 2>&1
cat "$release_dir/permissions-tests.log"
docker compose up -d --no-deps api
printf 'Release: %s\n' "$release_dir"
