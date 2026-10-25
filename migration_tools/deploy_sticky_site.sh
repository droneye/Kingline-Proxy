#!/bin/sh
set -eu
cd /opt/kingline-staging
release_dir=$(cat run/sticky-ru-release-path)
tar -xzf sticky-site-update.tar.gz
docker compose build api > "$release_dir/site-build.log" 2>&1
docker compose run --rm --no-deps -T api python - < ops/test_admin_access.py > "$release_dir/site-tests.log" 2>&1
cat "$release_dir/site-tests.log"
docker compose run --rm --no-deps -T api python -m unittest tests.test_guides >> "$release_dir/site-tests.log" 2>&1
docker compose up -d --no-deps api bot
