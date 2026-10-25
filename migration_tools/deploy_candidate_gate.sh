#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-control/before-candidate-gate-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/candidate-gate-release-path
python3 - "$release_dir" <<'PY'
import sqlite3,sys
from pathlib import Path
root=Path('/opt/kingline-manager/data');backup=Path(sys.argv[1])
with sqlite3.connect(root/'manager.sqlite3') as c:
 for table,condition in [('jobs',"state in ('queued','running','waiting_review')"),('vpn_changes',"state in ('queued_check','checking','queued_apply','applying')"),('ru_jobs',"state in ('queued','running')")]:
  assert not c.execute('select 1 from '+table+' where '+condition).fetchone(),'active_operation'
for name in ('manager.sqlite3','site-admin.sqlite3'):
 with sqlite3.connect('file:'+str(root/name)+'?mode=ro',uri=True) as src,sqlite3.connect(backup/name) as dst:
  src.backup(dst);assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
 (backup/name).chmod(0o600)
PY
tar --exclude=__pycache__ -czf "$release_dir/manager-before.tgz" -C /opt/kingline-manager/app node_manager
tar --exclude=__pycache__ -czf "$release_dir/site-before.tgz" site/src site/backend/infrastructure.py site/dist docs/service.md ops/test_admin_access.py
cp /opt/kingline-control/smart_dns.py "$release_dir/smart_dns.py"
docker image tag kingline-staging:20261022 kingline-staging:before-candidate-gate
tar -xzf /opt/kingline-control/candidate-manager.tgz -C /opt/kingline-manager/app
systemctl restart kingline-node-manager kingline-site-admin kingline-node-review-bot
curl -fsS --retry 8 --retry-all-errors --retry-delay 1 http://127.0.0.1:8090/healthz
# Native API contract and RF check are separate from a real fresh-VM installation.
/opt/kingline-manager/venv/bin/python /opt/kingline-control/verify_candidate_gate.py > "$release_dir/verification.log" 2>&1
cp /opt/kingline-control/smart_dns.candidate.py /opt/kingline-control/smart_dns.py
tar -xzf /opt/kingline-control/candidate-site.tgz -C /opt/kingline-staging
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T -v /opt/kingline-staging/ops:/ops:ro --entrypoint python api /ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api bot; then
 docker image tag kingline-staging:before-candidate-gate kingline-staging:20261022
 docker compose up -d --no-deps api bot
 exit 1
fi
printf '\nCandidate gate deployed; backups: %s\n' "$release_dir"
