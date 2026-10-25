#!/bin/sh
set -eu
cd /opt/kingline-control
release_dir="/opt/kingline-control/before-announcement-empty-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/announcement-empty-release-path
python3 - <<'PY'
from pathlib import Path
import hashlib,json
for name,digest in json.loads(Path('announcement-empty-baseline.json').read_text()).items():
 p=Path('/opt/kingline-staging')/name.removeprefix('staging/') if name.startswith('staging/') else Path('/opt/kingline-control')/name
 assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,'source_changed'
PY
cp subscription_modes.py subscription_gateway.py "$release_dir/"
tar --exclude=__pycache__ -czf "$release_dir/site-before.tgz" -C /opt/kingline-staging site/src/SubscriptionAnnouncement.jsx site/dist docs/service.md
docker image tag kingline-staging:20261022 kingline-staging:before-announcement-empty
rollback() {
 cp "$release_dir/subscription_modes.py" "$release_dir/subscription_gateway.py" /opt/kingline-control/
 tar -xzf "$release_dir/site-before.tgz" -C /opt/kingline-staging
 docker image tag kingline-staging:before-announcement-empty kingline-staging:20261022
 cd /opt/kingline-staging
 docker compose up -d --no-deps api > "$release_dir/rollback.log" 2>&1
 systemctl restart kingline-subscriptions
}
stage_dir="$release_dir/incoming"
mkdir -m 700 "$stage_dir"
tar -xzf /opt/kingline-control/announcement-empty-release.tgz -C "$stage_dir"
cp "$stage_dir/subscription_modes.py" "$stage_dir/subscription_gateway.py" /opt/kingline-control/
cp "$stage_dir/staging/site/src/SubscriptionAnnouncement.jsx" /opt/kingline-staging/site/src/
cp "$stage_dir/staging/docs/service.md" /opt/kingline-staging/docs/
cp -a "$stage_dir/staging/site/dist/." /opt/kingline-staging/site/dist/
cd /opt/kingline-staging
if ! docker compose build api > "$release_dir/build.log" 2>&1; then rollback; exit 1; fi
if ! docker compose up -d --no-deps --wait --wait-timeout 90 api > "$release_dir/start.log" 2>&1; then rollback; exit 1; fi
if ! systemctl restart kingline-subscriptions; then rollback; exit 1; fi
printf 'Empty-announcement update deployed\n'
