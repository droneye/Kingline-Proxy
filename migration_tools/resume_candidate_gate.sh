#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir=$(cat /opt/kingline-control/candidate-gate-release-path)
python3 - <<'PY'
import json
from pathlib import Path
r=json.loads(Path('/opt/kingline-control/candidate-gate-verification.json').read_text())
assert all(r.get(k) is True for k in ('isolated_phone_key','panel_resources_restored','service_probe_rights_restored','public_inventory_unchanged'))
assert r['real_rf']['ok'] and r['real_rf']['source_ok']
PY
cp /opt/kingline-control/smart_dns.candidate.py /opt/kingline-control/smart_dns.py
systemctl restart kingline-smart-dns
systemctl restart kingline-node-manager kingline-site-admin kingline-node-review-bot
tar -xzf /opt/kingline-control/candidate-site.tgz -C /opt/kingline-staging
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T -v /opt/kingline-staging/ops:/ops:ro --entrypoint python api /ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api bot; then
 docker image tag kingline-staging:before-candidate-gate kingline-staging:20261022
 docker compose up -d --no-deps api bot
 exit 1
fi
python3 - <<'PY'
import urllib.request,json
with urllib.request.urlopen('https://test.edge.kingline.online/api/health',timeout=20) as r:assert r.status==200
print(json.dumps({'site_healthy':True}))
PY
