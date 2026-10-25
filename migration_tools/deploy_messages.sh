#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-control/before-messages-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/messages-release-path
python3 - <<'PY'
from pathlib import Path
import hashlib
expected={'site/src/App.jsx':'932d8e30fd3820df8476c5495280027e51467863c433c1e97723dd7e255a91d2',
          'site/backend/staging_server.py':'6f10ef5a416992f76c424f9b133fe0d9ba6b680eaa6832c941c85d9103b0fcf0',
          'docs/service.md':'e9f2830b497b5a63ee56cb1c2bf26e74f00395e45998d55aeca10b0b6e3988d6'}
for name,digest in expected.items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,'source_changed'
previous=Path('/opt/kingline-control/messages-previous')
for name,digest in {'subscription_modes.py':'e41922beafd4343dedbad73d22c397743aef522c4de195854acb8a149aa5e7a4','subscription_gateway.py':'61c5d0f261714835f3653f19b945a41573cdfda4f7792394677a81f057e1bbff'}.items():
 assert hashlib.sha256((previous/name).read_bytes()).hexdigest()==digest,'previous_source_mismatch'
PY
tar --exclude=__pycache__ -czf "$release_dir/site-before.tgz" site/src site/backend/staging_server.py site/dist docs/service.md ops/test_admin_access.py
cp /opt/kingline-control/messages-previous/subscription_modes.py /opt/kingline-control/messages-previous/subscription_gateway.py "$release_dir/"
docker compose exec -T api python - <<'PY'
import os,sqlite3
from pathlib import Path
dest=Path('/data/before-messages.sqlite3')
assert not dest.exists(),'backup_already_exists'
with sqlite3.connect('file:'+os.environ['DB_PATH']+'?mode=ro',uri=True) as src,sqlite3.connect(dest) as dst:
 src.backup(dst);assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
dest.chmod(0o600)
print('Business backup integrity verified')
PY
mv data/before-messages.sqlite3 "$release_dir/business.sqlite3"
docker image tag kingline-staging:20261022 kingline-staging:before-messages
rollback() {
  tar -xzf "$release_dir/site-before.tgz" -C /opt/kingline-staging
  cp "$release_dir/subscription_modes.py" "$release_dir/subscription_gateway.py" /opt/kingline-control/
  docker image tag kingline-staging:before-messages kingline-staging:20261022
  docker compose up -d --no-deps api bot >/dev/null 2>&1
  systemctl restart kingline-subscriptions
}
trap 'rollback' HUP INT TERM
tar -xzf /opt/kingline-control/messages-site.tgz -C /opt/kingline-staging
if ! docker compose build api > "$release_dir/build.log" 2>&1; then rollback; exit 1; fi
if ! docker compose run --rm --no-deps -T -v /opt/kingline-staging/ops:/ops:ro --entrypoint python api /ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1; then rollback; exit 1; fi
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api bot > "$release_dir/start.log" 2>&1; then rollback; exit 1; fi
if ! systemctl restart kingline-subscriptions; then rollback; exit 1; fi
trap - HUP INT TERM
printf 'Messages deployed; server backup: %s\n' "$release_dir"
