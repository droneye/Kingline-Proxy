#!/usr/bin/env python3
import os
from pathlib import Path
import re
import secrets
import subprocess
import urllib.request

os.umask(0o077)
root = Path('/opt/remnawave')
root.mkdir(mode=0o700, exist_ok=True)
release = '3.4.5'
base = 'https://raw.githubusercontent.com/remnawave/backend/refs/tags/' + release + '/'

def fetch(name):
    with urllib.request.urlopen(base + name, timeout=30) as response:
        return response.read().decode()

compose = fetch('docker-compose-prod.yml')
compose = re.sub(r'image: remnawave/backend:[^\s]+', 'image: remnawave/backend:' + release, compose)
compose_file = root / 'docker-compose.yml'
if compose_file.exists():
    raise SystemExit('Existing panel compose file: refusing to overwrite')
compose_file.write_text(compose)
env = fetch('.env.sample')
database_password = secrets.token_hex(32)
settings = {
    'APP_SECRET': secrets.token_hex(64),
    'METRICS_PASS': secrets.token_hex(32),
    'WEBHOOK_SECRET_HEADER': secrets.token_hex(32),
    'POSTGRES_PASSWORD': database_password,
    'DATABASE_URL': '"postgresql://postgres:' + database_password + '@remnawave-db:5432/postgres"',
    'PANEL_DOMAIN': 'panel.kingline.online',
    'FRONT_END_DOMAIN': 'panel.kingline.online',
    'SUB_PUBLIC_DOMAIN': 'connect.kingline.online',
    'API_INSTANCES': '1',
    'IS_TELEGRAM_NOTIFICATIONS_ENABLED': 'false',
}
for key, value in settings.items():
    if re.search('^' + re.escape(key) + '=', env, flags=re.M):
        env = re.sub('^' + re.escape(key) + '=.*$', lambda match: key + '=' + value, env, flags=re.M)
    else:
        env += '\n' + key + '=' + value + '\n'
(root / '.env').write_text(env)
for name in ('docker-compose.yml', '.env'):
    (root / name).chmod(0o600)
subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=root, check=True)
print('Panel configuration ready; secrets stored on the core server only.')
subprocess.run(['docker', 'compose', 'up', '-d'], cwd=root, check=True)
