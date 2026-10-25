#!/bin/sh
set -eu
cd /opt/kingline-staging
python3 - <<'PY'
from pathlib import Path
import hashlib,json
for name,digest in json.loads(Path('/opt/kingline-control/online-compact-baseline.json').read_text()).items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest, 'source_changed: '+name
PY
release_dir="/opt/kingline-control/before-online-compact-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-control/online-compact-release-path
tar -czf "$release_dir/site-before.tgz" site/src/App.jsx site/src/admin-theme.css site/dist docs/service.md
docker image tag kingline-staging:20261022 kingline-staging:before-online-compact
rollback_on_error() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        cd /opt/kingline-staging
        tar -xzf "$release_dir/site-before.tgz"
        docker image tag kingline-staging:before-online-compact kingline-staging:20261022
        docker compose up -d --no-deps api > "$release_dir/rollback.log" 2>&1 || true
        printf 'Update failed; previous website restored.\n' >&2
    fi
    exit "$result"
}
trap rollback_on_error EXIT
mkdir -m 700 "$release_dir/incoming"
tar -xzf /opt/kingline-control/online-compact-release.tgz -C "$release_dir/incoming"
cp "$release_dir/incoming/site/src/App.jsx" site/src/
cp "$release_dir/incoming/site/src/admin-theme.css" site/src/
cp "$release_dir/incoming/docs/service.md" docs/
# Preserve previous static assets for tabs opened before this release.
cp -a "$release_dir/incoming/site/dist/." site/dist/
docker compose build api > "$release_dir/build.log" 2>&1
docker compose up -d --no-deps --wait --wait-timeout 60 api > "$release_dir/start.log" 2>&1
curl -fsS http://127.0.0.1:8788/api/health > "$release_dir/health.json"
trap - EXIT
printf 'Compact online list deployed. Rollback: %s\n' "$release_dir"
