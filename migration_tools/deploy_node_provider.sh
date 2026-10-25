#!/bin/sh
set -eu
cd /opt/kingline-staging
python3 - <<'PY'
from pathlib import Path
import hashlib,json
for name,digest in json.loads(Path('/opt/kingline-control/node-provider-baseline.json').read_text()).items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest, 'source_changed: '+name
PY
release_dir="/opt/kingline-control/before-node-provider-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/node-provider-release-path
python3 - "$release_dir" <<'PY'
import sqlite3,sys
from pathlib import Path
destination=Path(sys.argv[1])/'site-admin.sqlite3'
with sqlite3.connect('file:/opt/kingline-manager/data/site-admin.sqlite3?mode=ro',uri=True) as src,sqlite3.connect(destination) as dst:
    src.backup(dst)
    assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
destination.chmod(0o600)
PY
tar -czf "$release_dir/site-before.tgz" site/src/AdminInfrastructure.jsx site/src/admin-theme.css site/dist docs/service.md
cp /opt/kingline-manager/app/node_manager/site_admin.py "$release_dir/site_admin.py"
docker image tag kingline-staging:20261022 kingline-staging:before-node-provider
rollback_on_error() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        cd /opt/kingline-staging
        tar -xzf "$release_dir/site-before.tgz"
        cp "$release_dir/site_admin.py" /opt/kingline-manager/app/node_manager/site_admin.py
        # Restore only the newly introduced field; retain concurrent edits/secrets.
        python3 - "$release_dir" <<'PY'
import json,sqlite3,sys
from pathlib import Path
with sqlite3.connect('file:'+str(Path(sys.argv[1])/'site-admin.sqlite3')+'?mode=ro',uri=True) as old:
    previous={ip:json.loads(raw) for ip,raw in old.execute('select ip,metadata from inventory')}
with sqlite3.connect('/opt/kingline-manager/data/site-admin.sqlite3') as db:
    db.execute('begin immediate')
    for ip,raw in db.execute('select ip,metadata from inventory').fetchall():
        value=json.loads(raw)
        if 'provider_url' in previous.get(ip,{}):value['provider_url']=previous[ip]['provider_url']
        else:value.pop('provider_url',None)
        db.execute('update inventory set metadata=? where ip=?',(json.dumps(value,ensure_ascii=False),ip))
PY
        docker image tag kingline-staging:before-node-provider kingline-staging:20261022
        systemctl restart kingline-site-admin
        docker compose up -d --no-deps api > "$release_dir/rollback.log" 2>&1 || true
        printf 'Update failed; previous version restored.\n' >&2
    fi
    exit "$result"
}
trap rollback_on_error EXIT
mkdir -m 700 "$release_dir/incoming"
tar -xzf /opt/kingline-control/node-provider-release.tgz -C "$release_dir/incoming"
cp "$release_dir/incoming/node_manager/site_admin.py" /opt/kingline-manager/app/node_manager/site_admin.py
cp "$release_dir/incoming/staging/site/src/AdminInfrastructure.jsx" site/src/
cp "$release_dir/incoming/staging/site/src/admin-theme.css" site/src/
cp "$release_dir/incoming/staging/docs/service.md" docs/
cp -a "$release_dir/incoming/staging/site/dist/." site/dist/
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T -v /opt/kingline-staging/ops:/ops:ro --entrypoint python api /ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1
systemctl restart kingline-site-admin
curl -fsS --retry 5 --retry-all-errors --retry-delay 1 --unix-socket /run/kingline-site-admin/bridge.sock http://localhost/nodes > "$release_dir/nodes-after.json"
python3 - "$release_dir" <<'PY'
import json,sqlite3,sys,time
from pathlib import Path
nodes=json.loads((Path(sys.argv[1])/'nodes-after.json').read_text())['nodes']
urls={'198.51.100.31':'https://account.layer7.net/',
      '198.51.100.32':'https://app.webdock.io/',
      '198.51.100.33':'https://portal.serverq.net/',
      '198.51.100.34':'https://panel.quicksrv.io/',
      '198.51.100.35':'https://hpanel.hostinger.com/'}
assert set(urls)<=set(n['public_ip'] for n in nodes),'inventory_changed'
with sqlite3.connect('/opt/kingline-manager/data/site-admin.sqlite3') as db:
    db.execute('begin immediate')
    for ip,url in urls.items():
        row=db.execute('select metadata from inventory where ip=?',(ip,)).fetchone()
        value=json.loads(row[0]) if row else {}
        if value.get('provider_url'):continue
        value['provider_url']=url
        db.execute('insert into inventory(ip,metadata,secret,updated) values(?,?,NULL,?) on conflict(ip) do update set metadata=excluded.metadata,updated=excluded.updated', (ip,json.dumps(value,ensure_ascii=False),time.time()))
print('Provider links saved for five existing nodes; notes and credentials retained')
PY
docker compose up -d --no-deps --wait --wait-timeout 60 api > "$release_dir/start.log" 2>&1
curl -fsS http://127.0.0.1:8788/api/health > "$release_dir/health.json"
trap - EXIT
printf 'Provider links deployed. Rollback files: %s\n' "$release_dir"
