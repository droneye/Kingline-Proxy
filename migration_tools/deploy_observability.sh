#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/observability-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > run/observability-release-path
tar --exclude=__pycache__ -czf "$release_dir/code-before.tar.gz" site/src site/backend bot/remnawave.py bot/client_apps.json bot/handlers/instructions.py ops/collect_telemetry.py ops/test_admin_access.py site/dist
cp /opt/kingline-manager/app/node_manager/site_admin.py "$release_dir/site_admin.py"
docker image tag kingline-staging:20261022 kingline-staging:before-observability-20261023
systemctl stop kingline-preview-telemetry.timer kingline-preview-telemetry.service
tar -xzf observability-20261023.tar.gz 2> "$release_dir/tar.log"
cp site_admin.py /opt/kingline-manager/app/node_manager/site_admin.py
systemctl restart kingline-site-admin
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/tests.log" 2>&1
cat "$release_dir/tests.log"
docker compose up -d --no-deps api bot
systemctl start kingline-preview-telemetry.service kingline-preview-telemetry.timer
printf 'Release: %s\n' "$release_dir"
