#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import urllib.request

os.umask(0o077)
root = Path('/opt/remnawave')
credentials = json.loads((root / 'admin-credentials.json').read_text())
headers = {'Content-Type': 'application/json', 'Host': 'panel.kingline.online',
           'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1',
           'X-Remnawave-Client-Type': 'browser',
           'Authorization': 'Bearer ' + (root / '.bootstrap-token').read_text().strip()}
subscription = root / 'subscription'
subscription.mkdir(mode=0o700, exist_ok=True)
desired_scopes = ['system:metadata', 'subscriptions:read', 'subscription-page-configs:read']
old_token = json.loads((subscription / 'token-info.json').read_text()) if (subscription / 'token-info.json').exists() else None
if not (subscription / '.env').exists() or not old_token or set(old_token.get('scopes', [])) != set(desired_scopes):
    req = urllib.request.Request('http://127.0.0.1:3000/api/tokens', method='POST', headers=headers,
        data=json.dumps({'name': 'Subscription page', 'expiresInDays': 365,
                         'scopes': desired_scopes}).encode())
    with urllib.request.urlopen(req, timeout=30) as response:
        token = json.load(response)['response']
    (subscription / '.env').write_text('APP_PORT=3010\nREMNAWAVE_PANEL_URL=http://remnawave:3000\n'
        'REMNAWAVE_API_TOKEN=' + token['token'] + '\nTRUST_PROXY=1\n')
    (subscription / 'token-info.json').write_text(json.dumps({k:v for k,v in token.items() if k != 'token'}, indent=2))
    if old_token:
        req = urllib.request.Request('http://127.0.0.1:3000/api/tokens/' + old_token['uuid'], method='DELETE', headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            response.read()

caddy = root / 'caddy'
caddy.mkdir(mode=0o700, exist_ok=True)
entry = credentials['entry_token']
config = '''{
    servers {
        protocols h1 h2
    }
}
panel.kingline.online {
    encode zstd gzip
    header {
        Referrer-Policy "no-referrer"
        Cache-Control "no-store"
        X-Robots-Tag "noindex, nofollow, noarchive"
        -Server
    }
    handle /entry/ENTRY_TOKEN {
        header Set-Cookie "__Host-Kingline-Admin=ENTRY_TOKEN; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=604800"
        redir / 303
    }
    @admin header_regexp Cookie "(^|;[ ]*)__Host-Kingline-Admin=ENTRY_TOKEN(;|$)"
    handle @admin {
        reverse_proxy remnawave:3000
    }
    handle {
        respond "Not found" 404
    }
}
connect.kingline.online {
    encode zstd gzip
    header {
        Referrer-Policy "no-referrer"
        X-Robots-Tag "noindex, nofollow, noarchive"
        -Server
    }
    @withoutSubscription path / /robots.txt /favicon.ico
    handle @withoutSubscription {
        respond "Not found" 404
    }
    handle {
        reverse_proxy remnawave-subscription-page:3010
    }
}
'''.replace('ENTRY_TOKEN', entry)
(caddy / 'Caddyfile').write_text(config)
override = '''services:
  remnawave-subscription-page:
    image: remnawave/subscription-page:latest
    container_name: remnawave-subscription-page
    hostname: remnawave-subscription-page
    restart: always
    env_file: ./subscription/.env
    ports:
      - '127.0.0.1:3010:3010'
    networks:
      - remnawave-network
    depends_on:
      remnawave:
        condition: service_healthy
    logging:
      driver: local
      options:
        max-size: '10m'
        max-file: '3'
  caddy:
    image: caddy:2
    container_name: remnawave-caddy
    hostname: remnawave-caddy
    restart: always
    ports:
      - '0.0.0.0:80:80'
      - '0.0.0.0:443:443'
    networks:
      - remnawave-network
    volumes:
      - './caddy/Caddyfile:/etc/caddy/Caddyfile:ro'
      - caddy-data:/data
      - caddy-config:/config
    logging:
      driver: local
      options:
        max-size: '10m'
        max-file: '3'
volumes:
  caddy-data:
    name: remnawave-caddy-data
  caddy-config:
    name: remnawave-caddy-config
'''
(root / 'docker-compose.override.yml').write_text(override)
subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=root, check=True)
subprocess.run(['docker', 'compose', 'up', '-d'], cwd=root, check=True)
print('WEB_COMPONENTS_STARTED')
