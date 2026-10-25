"""Run on the new server only. No legacy services or production DNS touched."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

os.umask(0o077)
sys.path.insert(0, '/opt/kingline-manager/app')
from remna_api import RemnaAPI
sys.path.insert(0, '/opt/kingline-control')
from smart_dns import dns_api, rr, ZONE

root = Path('/opt/kingline-staging')
for directory in ['secrets', 'logs', 'run', 'data/mail', 'archive']:
    (root / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
api = RemnaAPI()
state = json.loads(Path('/opt/kingline-control/owner-profile.json').read_text())
squad = next((s for s in api.items('internal-squads', 'internalSquads') if s['name'] == 'Kingline Staging'), None)
if not squad:
    squad = api.call('internal-squads', 'POST', {'name': 'Kingline Staging', 'inbounds': [state['inbound_uuid']]})
if not (root / 'secrets/remna.token').exists():
    token = api.call('tokens', 'POST', {'name': 'Kingline staging users only', 'expiresInDays': 365,
                                     'scopes': ['users:read', 'users:write', 'subscriptions:read']})
    (root / 'secrets/remna.token').write_text(token['token'])
    (root / 'secrets/remna-token-info.json').write_text(json.dumps({k:v for k,v in token.items() if k != 'token'}, indent=2))
credentials = root / 'secrets/test-access.json'
if not credentials.exists():
    credentials.write_text(json.dumps({'site': 'https://test.edge.kingline.online',
        'gate_user': 'kingline', 'gate_password': secrets.token_urlsafe(20),
        'test_email': 'owner@example.invalid', 'test_password': secrets.token_urlsafe(20),
        'bot': 'https://t.me/stopRKNbot'}, indent=2))
env = {'BOT_TOKEN': (root / 'secrets/bot.token').read_text().strip(), 'ADMIN_ID': '100000001',
    'DB_PATH': '/data/vpn_bot.db', 'KINGLINE_DB_PATH': '/data/vpn_bot.db', 'KINGLINE_BOT_DIR': '/app/bot',
    'PROMOS_DB_PATH': '/data/promos.db', 'REFERRAL_DB_PATH': '/data/ref.db',
    'SUB_BASE': 'https://test.edge.kingline.online/sub/', 'SUB_BACKUP_BASE': 'https://test.edge.kingline.online/sub/',
    'KINGLINE_SITE_URL': 'https://test.edge.kingline.online', 'KINGLINE_TELEGRAM_BOT_URL': 'https://t.me/stopRKNbot',
    'KINGLINE_BOT_URL': 'https://t.me/stopRKNbot', 'REMNA_API_URL': 'http://remnawave:3000/api',
    'REMNA_TOKEN_FILE': '/run/secrets/remna.token', 'REMNA_SQUAD_UUID': squad['uuid'],
    'SUBSCRIPTION_INTERNAL_URL': 'http://subscription-guard:8080',
    'KINGLINE_DIAGNOSTICS_PATH': '/logs/diagnostics.jsonl', 'TELEMETRY_DB_PATH': '/logs/telemetry.db',
    'MAIL_CAPTURE_DIR': '/data/mail', 'KINGLINE_SUPPORT_UPLOAD_DIR': '/data/support_uploads',
    'KINGLINE_API_PORT': '8788', 'KINGLINE_API_HOST': '0.0.0.0', 'KINGLINE_SUBSCRIPTION_LOCK_DIR': '/run/kingline',
    'PYTHONPATH': '/app/bot:/app/site/backend', 'LOG_LEVEL': 'INFO', 'KINGLINE_STAGING': '1',
    'KINGLINE_DOMAIN_CATEGORY_CACHE_PATH': '/logs/domain_categories.cache.pkl'}
(root / '.env').write_text(''.join(k + '=' + v + '\n' for k,v in env.items()))
for directory in ['data', 'logs', 'run']:
    subprocess.run(['chown','-R','10001:10001', str(root / directory)], check=True)
subprocess.run(['chown','10001:10001', str(root / 'secrets/remna.token')], check=True)
for replica in json.loads(Path('/opt/kingline-control/dns-replicas.json').read_text()):
    dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': [rr('test.' + ZONE, 'A', ['198.51.100.10'], ttl=120)]})
print('Staging group, scoped token, isolated configuration and test DNS prepared.')
