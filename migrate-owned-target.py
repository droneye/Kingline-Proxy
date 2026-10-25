#!/usr/bin/env python3
"""Apply the already verified common HTTPS identity, with a complete config rollback."""
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/opt/kingline-manager/app')
from node_manager.cover import sync_dns
from node_manager.probe import verify_tunnel
from node_manager.store import Store, atomic_json
from remna_api import RemnaAPI

os.umask(0o077)
root, control = Path('/opt/kingline-manager/data'), Path('/opt/kingline-control')
store = Store(root)
with store.db() as db:
    assert not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone()
state = json.loads((control / 'owner-profile.json').read_text())
settings = json.loads((root / 'settings.json').read_text())
nodes = store.nodes()
prepared = json.loads((root / 'cover-prepared-nodes.json').read_text())
tests = json.loads((control / 'verification-owned-target-canary.json').read_text())
assert {n['public_ip'] for n in nodes} == {p['ip'] for p in prepared}
assert {n['public_ip'] for n in nodes} == {p['exit_ip'] for p in tests}
assert all(p['https'] and p['udp_dns'] and p['sni'] == 'edge.kingline.online' for p in tests)
api = RemnaAPI()
profile = api.call('config-profiles/' + state['profile_uuid'])
hosts = [h for h in api.items('hosts', 'hosts') if h['inbound']['configProfileUuid'] == state['profile_uuid']]
assert len(hosts) == len(nodes) + 1
backup = control / ('before-owned-target-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
for name, value in [('profile', profile), ('hosts', hosts), ('owner-profile', state), ('nodes', nodes), ('settings', settings)]:
    atomic_json(backup / (name + '.json'), value)
config = copy.deepcopy(profile['config'])
inbound = next(i for i in config['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
reality = inbound['streamSettings']['realitySettings']
reality.pop('dest', None)
reality['target'] = '127.0.0.1:8443'
reality['serverNames'] = ['edge.kingline.online']
candidate = control / 'candidate-owned-target.json'
atomic_json(candidate, config)
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '-v', str(candidate) + ':/tmp/config.json:ro', '--entrypoint', '/usr/local/bin/xray',
    settings['node_image'], 'run', '-test', '-config', '/tmp/config.json'], check=True,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(['systemctl', 'stop', 'kingline-node-manager'], check=True)
try:
    api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': profile['name'], 'config': config})
    for host in hosts:
        api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'sni': 'edge.kingline.online'})
    current = api.call('config-profiles/' + profile['uuid'])
    assert next(i for i in current['inbounds'] if i['tag'] == inbound['tag'])['uuid'] == state['inbound_uuid']
    updated_state = {**state, 'sni': 'edge.kingline.online', 'reality_target': '127.0.0.1:8443',
                     'camouflage_updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    atomic_json(control / 'owner-profile.json', updated_state)
    for node in nodes:
        node = dict(node)
        found = next(p for p in prepared if p['uuid'] == node['uuid'])
        node['cover'] = {k: v for k, v in found.items() if k not in ['uuid', 'ip']}
        store.save_node(node)
    atomic_json(control / 'nodes.json', store.nodes())
    sync_dns(control, store.nodes())
    atomic_json(control / 'cover-policy.json', {'domain': 'edge.kingline.online', 'target': '127.0.0.1:8443'})
    settings['cover'] = json.loads((root / 'cover-prepared.json').read_text())
    atomic_json(root / 'settings.json', settings)
    subprocess.run(['systemctl', 'restart', 'kingline-node-manager', 'kingline-smart-dns'], check=True)
    time.sleep(3)
    # Populate REALITY's per-target handshake cache before the final user-like
    # subscription check, just as onboarding does before publishing a new node.
    for node in nodes:
        verify_tunnel(control, node['public_ip'])
    subprocess.run(['python3', '/opt/kingline-control/verify-vpn.py'], check=True, timeout=180)
except Exception:
    api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': profile['name'], 'config': profile['config']})
    for host in hosts:
        api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'sni': host.get('sni')})
    atomic_json(control / 'owner-profile.json', state)
    for node in nodes:
        store.save_node(node)
    atomic_json(control / 'nodes.json', nodes)
    atomic_json(root / 'settings.json', json.loads((backup / 'settings.json').read_text()))
    (control / 'cover-policy.json').unlink(missing_ok=True)
    sync_dns(control, nodes)
    subprocess.run(['systemctl', 'restart', 'kingline-node-manager', 'kingline-smart-dns'], check=True)
    raise
atomic_json(control / 'owned-target-migration.json', {'domain': 'edge.kingline.online',
    'target': '127.0.0.1:8443', 'nodes': len(nodes), 'rollback_directory': str(backup), 'verified': True})
print('Owned HTTPS identity applied and subscription verified; backup:', backup)
