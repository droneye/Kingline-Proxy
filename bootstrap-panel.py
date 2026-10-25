#!/usr/bin/env python3
import json
import os
from pathlib import Path
import secrets
import urllib.request
import urllib.error

os.umask(0o077)
root = Path('/opt/remnawave')
headers = {'Content-Type': 'application/json', 'Host': 'panel.kingline.online',
           'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1'}

def api(path, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request('http://127.0.0.1:3000/api/' + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)['response']
    except urllib.error.HTTPError as error:
        raise RuntimeError(path + ': ' + str(error.code) + ' ' + error.read().decode()[:1500]) from None

credentials_file = root / 'admin-credentials.json'
if credentials_file.exists():
    credentials = json.loads(credentials_file.read_text())
else:
    credentials = {'username': 'admin', 'password': secrets.token_urlsafe(30) + 'Aa7',
                   'entry_token': secrets.token_hex(32), 'panel_url': 'https://panel.kingline.online',
                   'subscription_url': 'https://connect.kingline.online'}
    credentials_file.write_text(json.dumps(credentials, indent=2) + '\n')
    credentials_file.chmod(0o600)
status = api('auth/status')
action = 'register' if status['isRegisterAllowed'] else 'login'
token = api('auth/' + action, 'POST', {key: credentials[key] for key in ('username', 'password')})['accessToken']
headers['Authorization'] = 'Bearer ' + token
(root / '.bootstrap-token').write_text(token)
headers['X-Remnawave-Client-Type'] = 'browser'
provision_file = root / '.provision-api.json'
if provision_file.exists():
    provision_token = json.loads(provision_file.read_text())
else:
    provision_token = api('tokens', 'POST', {'name': 'Initial infrastructure setup', 'expiresInDays': 1, 'scopes': ['*']})
    provision_file.write_text(json.dumps(provision_token))
headers.pop('X-Remnawave-Client-Type')
headers['Authorization'] = 'Bearer ' + provision_token['token']
(root / '.node-secret').write_text(api('keygen')['secretKey'])
print('Admin configured; registration allowed:', api('auth/status')['isRegisterAllowed'])

profiles = api('config-profiles')
if isinstance(profiles, dict):
    profiles = profiles.get('configProfiles', profiles.get('items', []))
profile = next((p for p in profiles if p['name'] == 'Infrastructure staging'), None)
if not profile:
    profile = api('config-profiles', 'POST', {
        'name': 'Infrastructure staging',
        'config': {
            'log': {'loglevel': 'warning'},
            'inbounds': [{'tag': 'STAGING_LOCAL_ONLY', 'listen': '127.0.0.1', 'port': 14430,
                          'protocol': 'vless', 'settings': {'clients': [], 'decryption': 'none'}}],
            'outbounds': [{'tag': 'DIRECT', 'protocol': 'freedom'}, {'tag': 'BLOCK', 'protocol': 'blackhole'}],
            'routing': {'rules': [{'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'BLOCK'}]},
        },
    })
nodes = api('nodes')
if isinstance(nodes, dict):
    nodes = nodes.get('nodes', nodes.get('items', []))
for name, address, country, note in (
    ('DE-Germany-01', '10.77.0.2', 'DE', '198.51.100.31; 5 TB/month; EUR 4.90/month'),
    ('DK-Denmark-01', '10.77.0.3', 'DK', '198.51.100.32; 1 TB/month; EUR 2.15/month'),
):
    node = next((n for n in nodes if n['name'] == name), None)
    if not node:
        node = api('nodes', 'POST', {
            'name': name, 'address': address, 'port': 2222, 'countryCode': country,
            'isTrafficTrackingActive': False, 'note': note,
            'configProfile': {'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [i['uuid'] for i in profile['inbounds']]},
        })
    print('Node registered:', name, address, node['uuid'])

headers['Authorization'] = 'Bearer ' + token
headers['X-Remnawave-Client-Type'] = 'browser'
scopes = api('tokens/scopes')
(root / 'available-api-scopes.json').write_text(json.dumps(scopes, indent=2))
print('API scope catalog saved.')
