#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/ru-admin-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > run/ru-admin-release-path
python3 - "$release_dir" <<'PY'
from pathlib import Path
import json,os,shutil,sqlite3,sys
folder=Path(sys.argv[1]);manager=Path('/opt/kingline-manager/data')
with sqlite3.connect(manager/'manager.sqlite3') as db:
 assert db.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued')").fetchone()[0]==0,'active_installation'
 assert db.execute("SELECT count(*) FROM vpn_changes WHERE state IN ('queued_check','checking','queued_apply','applying')").fetchone()[0]==0,'active_vpn_change'
 if db.execute("SELECT name FROM sqlite_master WHERE name='ru_jobs'").fetchone():
  assert db.execute("SELECT count(*) FROM ru_jobs WHERE state IN ('running','queued')").fetchone()[0]==0,'active_ru_change'
for src in [manager/'manager.sqlite3',manager/'site-admin.sqlite3',*[Path('/opt/kingline-staging/data')/n for n in ('vpn_bot.db','ref.db','promos.db')]]:
 if not src.exists():continue
 with sqlite3.connect('file:'+str(src)+'?mode=ro',uri=True) as source,sqlite3.connect(folder/src.name) as dest:
  source.backup(dest);assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 (folder/src.name).chmod(0o600)
control=Path('/opt/kingline-control')
for name in ('ru-egress.json','owner-profile.json','nodes.json','node-paths.json','sticky-policy.json'):
 if (control/name).exists():shutil.copyfile(control/name,folder/name);(folder/name).chmod(0o600)
PY
tar --exclude=__pycache__ -czf "$release_dir/site-code.tar.gz" site/src site/backend bot/handlers/instructions.py site/dist Dockerfile ops/backup.py ops/test_admin_access.py
if [ -d docs ]; then tar -czf "$release_dir/docs-before.tar.gz" docs; fi
tar --exclude=__pycache__ -czf "$release_dir/manager-code.tar.gz" -C /opt/kingline-manager/app node_manager
docker image tag kingline-staging:20261022 kingline-staging:before-ru-admin-20261023
systemctl stop kingline-ru-egress.timer
trap 'systemctl start kingline-ru-egress.timer' EXIT
# Let the current maintenance pass finish before updating its module.
for i in $(seq 1 45); do
 if ! systemctl is-active --quiet kingline-ru-egress.service; then break; fi
 sleep 1
done
if systemctl is-active --quiet kingline-ru-egress.service; then echo 'RU maintenance still busy'; exit 1; fi
tar -xzf /opt/kingline-manager/manager-ru-admin.tar.gz -C /opt/kingline-manager/app
systemctl restart kingline-node-manager kingline-site-admin
if ! curl --silent --fail --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:8090/healthz; then
 tar -xzf "$release_dir/manager-code.tar.gz" -C /opt/kingline-manager/app
 systemctl restart kingline-node-manager kingline-site-admin
 exit 1
fi
tar -xzf staging-ru-admin.tar.gz
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/permissions-tests.log" 2>&1
cat "$release_dir/permissions-tests.log"
docker compose run --rm --no-deps -T api python -m unittest tests.test_guides > "$release_dir/guide-tests.log" 2>&1
cat "$release_dir/guide-tests.log"
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api bot; then
 docker image tag kingline-staging:before-ru-admin-20261023 kingline-staging:20261022
 docker compose up -d --no-deps api bot
 exit 1
fi
systemctl start kingline-ru-egress.timer
python3 ops/backup.py
printf '\nRelease: %s\n' "$release_dir"
