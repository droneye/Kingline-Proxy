#!/bin/sh
set -eu
release_dir="/opt/kingline-staging/backups/sticky-ru-$(date -u +%Y%m%d-%H%M%S)"
mkdir -m 700 "$release_dir"
printf '%s\n' "$release_dir" > /opt/kingline-staging/run/sticky-ru-release-path
cp /opt/kingline-control/smart_dns.py /opt/remnawave/subscription-guard/nginx.conf /opt/remnawave/docker-compose.override.yml "$release_dir/"
tar -czf "$release_dir/site-before.tar.gz" -C /opt/kingline-staging site/src site/backend bot/handlers/instructions.py bot/client_apps.json site/dist
docker image tag kingline-staging:20261022 kingline-staging:before-sticky-ru-20261023
cp /opt/kingline-control/smart_dns.sticky.py /opt/kingline-control/smart_dns.py
cp /opt/kingline-control/kingline-subscriptions.service /etc/systemd/system/kingline-subscriptions.service
printf '{"enabled":true,"lease_seconds":86400}\n' > /opt/kingline-control/sticky-policy.json
systemctl stop kingline-smart-dns
python3 /opt/kingline-control/smart_dns.py --once > "$release_dir/dns-initial.json"
systemctl start kingline-smart-dns
systemctl daemon-reload
systemctl enable --now kingline-subscriptions
python3 - <<'PY'
from pathlib import Path
p=Path('/opt/remnawave/subscription-guard/nginx.conf');s=p.read_text()
s=s.replace('http {','http {\n    upstream kingline_subscriptions { server unix:/run/kingline-subscriptions/gateway.sock; keepalive 8; }',1)
s=s.replace('            set $subscription_backend remnawave-subscription-page:3010;\n','')
assert 'proxy_pass http://$subscription_backend;' in s
s=s.replace('proxy_pass http://$subscription_backend;','proxy_pass http://kingline_subscriptions;')
p.write_text(s)
p=Path('/opt/remnawave/docker-compose.override.yml');s=p.read_text();needle='      - ./subscription-guard/nginx.conf:/etc/nginx/nginx.conf:ro'
assert needle in s and '/run/kingline-subscriptions' not in s
s=s.replace(needle,needle+'\n      - /run/kingline-subscriptions:/run/kingline-subscriptions:ro')
p.write_text(s)
PY
cd /opt/remnawave
docker compose config --quiet
docker compose up -d --no-deps --force-recreate subscription-guard
docker exec kingline-subscription-guard nginx -t
printf 'Release: %s\n' "$release_dir"
