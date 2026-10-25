#!/bin/sh
set -eu
cd /opt/kingline-control
release_dir="/opt/kingline-control/before-profile-title-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/profile-title-release-path
python3 - <<'PY'
from pathlib import Path
import hashlib,json
for name,digest in json.loads(Path('profile-title-baseline.json').read_text()).items():
 p=Path('/opt/kingline-staging')/name.removeprefix('staging/') if name.startswith('staging/') else Path('/opt/kingline-control')/name
 assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,'source_changed'
PY
cp subscription_modes.py subscription_gateway.py "$release_dir/"
tar --exclude=__pycache__ -czf "$release_dir/site-before.tgz" -C /opt/kingline-staging site/backend/staging_server.py docs/service.md ops/test_admin_access.py
docker image tag kingline-staging:20261022 kingline-staging:before-profile-title
rollback() {
 cp "$release_dir/subscription_modes.py" "$release_dir/subscription_gateway.py" /opt/kingline-control/
 tar -xzf "$release_dir/site-before.tgz" -C /opt/kingline-staging
 docker image tag kingline-staging:before-profile-title kingline-staging:20261022
 cd /opt/kingline-staging
 docker compose up -d --no-deps api > "$release_dir/rollback.log" 2>&1
 systemctl restart kingline-subscriptions
}
stage_dir="$release_dir/incoming"
mkdir -m 700 "$stage_dir"
tar -xzf /opt/kingline-control/profile-title-release.tgz -C "$stage_dir"
cp "$stage_dir/subscription_modes.py" "$stage_dir/subscription_gateway.py" /opt/kingline-control/
cp "$stage_dir/staging/site/backend/staging_server.py" /opt/kingline-staging/site/backend/
cp "$stage_dir/staging/docs/service.md" /opt/kingline-staging/docs/
cp "$stage_dir/staging/ops/test_admin_access.py" /opt/kingline-staging/ops/
cd /opt/kingline-staging
if ! docker compose build api > "$release_dir/build.log" 2>&1; then rollback; exit 1; fi
if ! docker compose run --rm --no-deps -T -v /opt/kingline-staging/ops:/ops:ro --entrypoint python api /ops/test_admin_access.py > "$release_dir/admin-tests.log" 2>&1; then rollback; exit 1; fi
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api > "$release_dir/start.log" 2>&1; then rollback; exit 1; fi
if ! systemctl restart kingline-subscriptions; then rollback; exit 1; fi
printf 'Personal subscription title deployed\n'
