#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir="/opt/kingline-staging/backups/release-followup-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$release_dir"
chmod 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-staging/run/followup-release-path
docker image tag kingline-staging:20261022 kingline-staging:before-followup-20261023
tar --exclude=__pycache__ -czf "$release_dir/site-code.tar.gz" bot site/backend site/src compose.yaml Dockerfile
cp /opt/kingline-manager/app/node_manager/site_admin.py "$release_dir/site_admin.py"
mv site/dist "$release_dir/dist"
tar -xzf staging-followup-20261023.tar.gz
cp /opt/kingline-manager/site_admin.followup.py /opt/kingline-manager/app/node_manager/site_admin.py
systemctl restart kingline-site-admin
docker compose build api > "$release_dir/build.log" 2>&1
docker compose run --rm --no-deps -T api python -m unittest tests.test_guides > "$release_dir/guide-tests.log" 2>&1
docker compose up -d
systemctl is-active kingline-site-admin kingline-node-manager
printf 'Release: %s\n' "$release_dir"
