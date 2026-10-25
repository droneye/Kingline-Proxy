"""Isolated, temporary panel resources; never attach test inbounds to live VPN nodes."""
import base64
import copy
import datetime
import json
from pathlib import Path
import secrets
import socket
import urllib.parse
import urllib.request

from remna_api import RemnaAPI

root = Path('/opt/kingline-control')
api = RemnaAPI()
state = json.loads((root / 'owner-profile.json').read_text())
suffix = secrets.token_hex(4)
created = {'users': [], 'hosts': [], 'nodes': [], 'internal-squads': [], 'config-profiles': []}
original_ids = {kind: {x['uuid'] for x in api.items(kind, key)} for kind, key in [
    ('hosts', 'hosts'), ('nodes', 'nodes'), ('internal-squads', 'internalSquads'), ('config-profiles', 'configProfiles')]}
original_ids['users'] = {x['id'] for x in api.call('users').get('users', [])}
report = {'test': 'isolated Remnawave API and subscription publication', 'checks': []}


def create(kind, body):
    value = api.call(kind, 'POST', body)
    identity = value['id'] if kind == 'users' else value['uuid']
    assert identity not in original_ids[kind], 'Refusing any operation on an existing resource'
    created[kind].append(identity)
    return value


def links(user):
    req = urllib.request.Request('https://connect.kingline.online/' + user['shortUuid'],
                                 headers={'User-Agent': 'v2raytun/2.4.7'})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read()
    try:
        text = base64.b64decode(data).decode()
    except Exception:
        text = data.decode()
    return [urllib.parse.urlsplit(x) for x in text.splitlines() if x.startswith('vless://')]


try:
    with socket.socket() as s:
        assert s.connect_ex(('127.0.0.1', 65534)) != 0, 'test management port occupied'
    original = api.call('config-profiles/' + state['profile_uuid'])
    config = copy.deepcopy(original['config'])
    config['inbounds'][0]['listen'] = '127.0.0.1'
    config['inbounds'][0]['port'] = 14431
    config['inbounds'][0]['tag'] = 'KL_TEST_' + suffix
    profile = create('config-profiles', {'name': 'KL-test-' + suffix, 'config': config})
    inbound = profile['inbounds'][0]['uuid']
    node = create('nodes', {'name': 'KL-test-' + suffix, 'address': '127.0.0.1', 'port': 65534,
        'countryCode': 'FI', 'isTrafficTrackingActive': True, 'trafficLimitBytes': 5 * 10**12,
        'trafficResetDay': 19, 'notifyPercent': 80,
        'configProfile': {'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [inbound]}})
    report['checks'].append('node creation with quota and standard profile schema accepted')
    squad = create('internal-squads', {'name': 'KL-test-' + suffix, 'inbounds': [inbound]})
    users = [create('users', {'username': 'kl_test_' + suffix + '_' + str(i), 'status': 'ACTIVE',
              'expireAt': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)).isoformat(),
              'trafficLimitBytes': 0, 'activeInternalSquads': [squad['uuid']]}) for i in range(2)]
    common = {'inbound': {'configProfileUuid': profile['uuid'], 'configProfileInboundUuid': inbound},
              'port': 443, 'fingerprint': 'firefox', 'sni': state['sni'], 'nodes': [node['uuid']], 'isDisabled': False}
    first = create('hosts', {**common, 'remark': 'API test first', 'address': '192.0.2.10'})
    assert all(len(links(u)) == 1 for u in users)
    second = create('hosts', {**common, 'remark': 'API test second', 'address': '192.0.2.11'})
    for user in users:
        records = links(user)
        assert len(records) == 2 and {p.hostname for p in records} == {'192.0.2.10', '192.0.2.11'}
        for record in records:
            params = urllib.parse.parse_qs(record.query)
            assert params['fp'] == ['firefox'] and params['flow'] == ['xtls-rprx-vision']
            assert params['type'][0] in ['tcp', 'raw']
    report['checks'].append('new location appeared automatically in both existing test-user subscriptions')
finally:
    failed = []
    for kind, ids in created.items():
        for identity in reversed(ids):
            try:
                assert identity not in original_ids[kind], 'Existing resources must never be deleted'
                api.call(kind + '/' + str(identity), 'DELETE')
            except Exception:
                failed.append({'kind': kind, 'uuid': identity})
    report['cleanup_complete'] = not failed
    report['remaining_test_resources'] = failed
    (root / 'verification-manager-panel.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    assert not failed, 'Some isolated test resources need cleanup'
