#!/usr/bin/env python3
"""Run on the control server with remna_api.py available."""
import base64
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
from remna_api import RemnaAPI

os.umask(0o077)
root = Path('/opt/kingline-control')
api = RemnaAPI()
state_file = root / 'owner-profile.json'
if state_file.exists():
    state = json.loads(state_file.read_text())
else:
    key_program = """const c=require('crypto');const k=c.generateKeyPairSync('x25519');
console.log(JSON.stringify({privateKey:k.privateKey.export({type:'pkcs8',format:'der'}).subarray(-32).toString('base64url'),publicKey:k.publicKey.export({type:'spki',format:'der'}).subarray(-32).toString('base64url')}));"""
    keys = json.loads(subprocess.check_output(['docker', 'exec', 'remnawave', 'node', '-e', key_program]))
    state = {'reality': keys, 'short_id': secrets.token_hex(8),
             'path': '/c/' + secrets.token_urlsafe(12), 'sni': 'www.samsung.com',
             'subscription_code': secrets.token_urlsafe(16)}
    state_file.write_text(json.dumps(state, indent=2))

profile = next((p for p in api.items('config-profiles', 'configProfiles')
                if p['name'] == 'Kingline XHTTP REALITY'), None)
if profile is None:
    config = {
        'log': {'access': 'none', 'loglevel': 'warning'},
        'dns': {'servers': ['localhost'], 'queryStrategy': 'UseIPv4'},
        'inbounds': [{
            'tag': 'KINGLINE_XHTTP_REALITY', 'listen': '0.0.0.0', 'port': 443,
            'protocol': 'vless', 'settings': {'clients': [], 'decryption': 'none'},
            'streamSettings': {
                'network': 'xhttp', 'security': 'reality',
                'realitySettings': {'show': False, 'target': state['sni'] + ':443', 'xver': 0,
                    'serverNames': [state['sni']], 'privateKey': state['reality']['privateKey'],
                    'shortIds': [state['short_id']], 'maxTimeDiff': 120000},
                'xhttpSettings': {'path': state['path'], 'mode': 'auto'},
                'sockopt': {'tcpKeepAliveIdle': 60, 'tcpKeepAliveInterval': 30},
            },
        }],
        'outbounds': [{'tag': 'DIRECT', 'protocol': 'freedom'},
                      {'tag': 'BLOCK', 'protocol': 'blackhole'}],
        'routing': {'domainStrategy': 'IPIfNonMatch', 'rules': [
            {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'BLOCK'},
        ]},
    }
    profile = api.call('config-profiles', 'POST', {'name': 'Kingline XHTTP REALITY', 'config': config})
inbound = next(i for i in profile['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
state['profile_uuid'] = profile['uuid']
state['inbound_uuid'] = inbound['uuid']
state_file.write_text(json.dumps(state, indent=2))

nodes = api.items('nodes', 'nodes')
inventory = []
for name, address, quota in [('DE-Germany-01', '198.51.100.31', 5_000_000_000_000),
                             ('DK-Denmark-01', '198.51.100.32', 1_000_000_000_000)]:
    node = next(n for n in nodes if n['name'] == name)
    api.call('nodes', 'PATCH', {
        'uuid': node['uuid'], 'isTrafficTrackingActive': True, 'trafficLimitBytes': quota,
        'trafficResetDay': 19, 'notifyPercent': 80,
        'configProfile': {'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [inbound['uuid']]},
    })
    inventory.append({'name': name, 'uuid': node['uuid'], 'public_ip': address,
                      'management_ip': node['address'], 'quota_bytes': quota, 'reset_day': 19})
(root / 'nodes.json').write_text(json.dumps(inventory, indent=2))

squad = next((s for s in api.items('internal-squads', 'internalSquads')
              if s['name'] == 'Kingline Personal'), None)
if squad is None:
    squad = api.call('internal-squads', 'POST', {'name': 'Kingline Personal', 'inbounds': [inbound['uuid']]})

hosts = api.items('hosts', 'hosts')
desired = [('⚡ Умный выбор', 'auto.edge.kingline.online', [n['uuid'] for n in inventory]),
           ('🇩🇪 Германия', '198.51.100.31', [inventory[0]['uuid']]),
           ('🇩🇰 Дания', '198.51.100.32', [inventory[1]['uuid']])]
for remark, address, node_ids in desired:
    if not any(host['remark'] == remark for host in hosts):
        api.call('hosts', 'POST', {
            'inbound': {'configProfileUuid': profile['uuid'], 'configProfileInboundUuid': inbound['uuid']},
            'remark': remark, 'address': address, 'port': 443, 'fingerprint': 'chrome',
            'sni': state['sni'], 'nodes': node_ids, 'isDisabled': False,
        })

if 'user' not in state:
    state['user'] = api.call('users', 'POST', {
        'username': 'vladislav', 'shortUuid': state['subscription_code'], 'status': 'ACTIVE',
        'expireAt': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)).isoformat(),
        'trafficLimitBytes': 0, 'activeInternalSquads': [squad['uuid']],
        'description': 'Owner test profile: smart DNS, Germany, Denmark',
        'tag': 'OWNER_TEST',
    })
    state_file.write_text(json.dumps(state, indent=2))

settings = api.call('subscription-settings')
api.call('subscription-settings', 'PATCH', {
    'uuid': settings['uuid'], 'randomizeHosts': False, 'isShowCustomRemarks': False,
    'customResponseHeaders': {
        **settings.get('customResponseHeaders', {}),
        'profile-title': 'base64:' + base64.b64encode('Kingline · Владислав'.encode()).decode(),
        'profile-update-interval': '1',
    },
})

read_token = root / 'nodes-read.token'
if not read_token.exists():
    token = api.call('tokens', 'POST', {'name': 'Smart DNS quota reader', 'expiresInDays': 365,
                                      'scopes': ['nodes:read']})
    read_token.write_text(token['token'])
    (root / 'nodes-read-token-info.json').write_text(json.dumps(
        {k: v for k, v in token.items() if k != 'token'}, indent=2))
print('Owner account ready; three connection profiles created; monthly node quotas reset on day 19')
