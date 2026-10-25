#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/ru-watch-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > run/ru-watch-release-path
python3 - "$release_dir" <<'PY'
from pathlib import Path
import os,sqlite3,sys
folder=Path(sys.argv[1]);manager=Path('/opt/kingline-manager/data')
with sqlite3.connect(manager/'manager.sqlite3') as c:
 for table,condition in [('jobs',"state in ('queued','running')"),('vpn_changes',"state in ('queued_check','checking','queued_apply','applying')"),('ru_jobs',"state in ('queued','running')")]:assert not c.execute('select 1 from '+table+' where '+condition).fetchone(),'active_operation'
for source in [manager/'manager.sqlite3',manager/'site-admin.sqlite3',Path('/opt/kingline-staging/data/vpn_bot.db')]:
 with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as src,sqlite3.connect(folder/source.name) as dst:
  src.backup(dst);assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
 (folder/source.name).chmod(0o600)
PY
tar --exclude=__pycache__ -czf "$release_dir/site-before.tar.gz" site/src site/dist site/backend bot/staging_main.py bot/handlers docs compose.yaml ops/backup.py ops/test_admin_access.py
tar --exclude=__pycache__ -czf "$release_dir/manager-before.tar.gz" -C /opt/kingline-manager/app node_manager
cp /opt/kingline-control/smart_dns.py "$release_dir/smart_dns.py"
docker image tag kingline-staging:20261022 kingline-staging:before-ru-watch
# Configure an initially disabled, independent home probe; no credentials printed.
/opt/kingline-manager/venv/bin/python - <<'PY'
from pathlib import Path
import json,subprocess,os
control=Path('/opt/kingline-control');file=control/'ru-monitor.json'
if not file.exists():
 state=json.loads((control/'ru-egress.json').read_text())
 output=subprocess.check_output(['docker','exec','kingline-staging-bot-1','python','-c',"from config import ADMIN_ID; import json; print(json.dumps({'owner':ADMIN_ID}))"],text=True)
 owner=json.loads(output.splitlines()[-1])['owner'];assert owner>0
 body={'enabled':False,'probe':state['pi'],'source_public_ip':state['public_ip'],'owner_id':owner,'binary_container':'kingline-ru-remnanode'+('-'+str(state['generation']) if state.get('generation') else '')}
 file.write_text(json.dumps(body));file.chmod(0o600)
PY
tar -xzf /opt/kingline-manager/manager-ru-watch.tar.gz -C /opt/kingline-manager/app
cp /opt/kingline-manager/app/node_manager/kingline-node-review-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart kingline-node-manager kingline-site-admin
curl --silent --show-error --fail --retry 8 --retry-all-errors --retry-delay 1 http://127.0.0.1:8090/healthz
systemctl enable --now kingline-node-review-bot
cp /opt/kingline-manager/smart_dns.ru-watch.py /opt/kingline-control/smart_dns.py
systemctl restart kingline-smart-dns
tar -xzf staging-ru-watch.tar.gz
docker compose config --quiet
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1
cat "$release_dir/admin-tests.log"
docker compose run --rm --no-deps -T api python -m unittest tests.test_node_reviews > "$release_dir/bot-review-tests.log" 2>&1
cat "$release_dir/bot-review-tests.log"
docker compose run --rm --no-deps -T api python -m unittest tests.test_guides > "$release_dir/guide-tests.log" 2>&1
cat "$release_dir/guide-tests.log"
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api bot; then
 docker image tag kingline-staging:before-ru-watch kingline-staging:20261022
 docker compose up -d --no-deps api bot
 exit 1
fi
# Confirm the exact diagnostic asset before enabling measurements/notifications.
python3 - <<'PY'
import hashlib,urllib.request
url='https://test.edge.kingline.online/api/connectivity/probe.bin'
with urllib.request.urlopen(url,timeout=20) as r:body=r.read(1048577);assert r.status==200 and r.headers['X-Kingline-Probe-IP']
expected=hashlib.shake_256(b'Kingline connectivity probe v1').digest(1048576)
assert body==expected,'diagnostic_asset_mismatch'
with urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=0-2047'}),timeout=20) as r:assert r.status==206 and r.read()==expected[:2048]
print('Diagnostic asset and partial transfer verified.')
PY
/opt/kingline-manager/venv/bin/python - <<'PY'
from pathlib import Path
import json,sys
sys.path.insert(0,'/opt/kingline-manager/app')
from node_manager.store import atomic_json
file=Path('/opt/kingline-control/ru-monitor.json');body=json.loads(file.read_text());body['enabled']=True;atomic_json(file,body)
PY
systemctl restart kingline-node-manager kingline-site-admin kingline-node-review-bot
curl --silent --show-error --fail --retry 8 --retry-all-errors --retry-delay 1 http://127.0.0.1:8090/healthz
printf '\nRelease: %s\n' "$release_dir"
