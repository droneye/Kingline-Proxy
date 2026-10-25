#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-control/before-node-billing-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/node-billing-release-path
python3 - "$release_dir" <<'PY'
from pathlib import Path
import hashlib,json,sqlite3,sys
roots={'manager':Path('/opt/kingline-manager/app/node_manager'),'site':Path('/opt/kingline-staging')}
for kind,files in json.loads(Path('/opt/kingline-control/node-billing-baseline.json').read_text()).items():
 for name,digest in files.items():assert hashlib.sha256((roots[kind]/name).read_bytes()).hexdigest()==digest,'source_changed'
root=Path('/opt/kingline-manager/data');backup=Path(sys.argv[1])
with sqlite3.connect(root/'manager.sqlite3') as c:
 for table,condition in [('jobs',"state in ('queued','running')"),('vpn_changes',"state in ('queued_check','checking','queued_apply','applying')"),('ru_jobs',"state in ('queued','running')")]:
  assert not c.execute('select 1 from '+table+' where '+condition).fetchone(),'active_operation'
for name in ('manager.sqlite3','site-admin.sqlite3'):
 with sqlite3.connect('file:'+str(root/name)+'?mode=ro',uri=True) as src,sqlite3.connect(backup/name) as dst:
  src.backup(dst);assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
 (backup/name).chmod(0o600)
print('Manager backups verified; no active infrastructure change')
PY
tar --exclude=__pycache__ -czf "$release_dir/manager-before.tgz" -C /opt/kingline-manager/app node_manager
tar --exclude=__pycache__ -czf "$release_dir/site-before.tgz" bot/staging_main.py site/src/AdminInfrastructure.jsx site/dist docs/service.md ops/test_admin_access.py
docker image tag kingline-staging:20261022 kingline-staging:before-node-billing
rollback() {
 tar -xzf "$release_dir/manager-before.tgz" -C /opt/kingline-manager/app
 tar -xzf "$release_dir/site-before.tgz" -C /opt/kingline-staging
 docker image tag kingline-staging:before-node-billing kingline-staging:20261022
 systemctl restart kingline-node-manager kingline-site-admin kingline-node-review-bot
 docker compose up -d --no-deps api bot > "$release_dir/rollback.log" 2>&1
}
tar -xzf /opt/kingline-control/node-billing-manager.tgz -C /opt/kingline-manager/app
tar -xzf /opt/kingline-control/node-billing-site.tgz -C /opt/kingline-staging
if ! docker compose build api > "$release_dir/build.log" 2>&1; then rollback; exit 1; fi
if ! docker compose run --rm --no-deps -T -v /opt/kingline-staging/ops:/ops:ro --entrypoint python api /ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1; then rollback; exit 1; fi
if ! docker compose run --rm --no-deps -T --entrypoint python api -m unittest discover -s /app/bot/tests -p 'test_node_*.py' > "$release_dir/bot-tests.log" 2>&1; then rollback; exit 1; fi
if ! systemctl restart kingline-node-manager kingline-site-admin kingline-node-review-bot; then rollback; exit 1; fi
if ! curl -fsS --retry 8 --retry-all-errors --retry-delay 1 http://127.0.0.1:8090/healthz > "$release_dir/manager-health.json"; then rollback; exit 1; fi
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api bot > "$release_dir/start.log" 2>&1; then rollback; exit 1; fi
printf 'Server payment reminders deployed\n'
