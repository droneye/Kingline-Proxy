#!/usr/bin/env python3
"""Restore the last owner-confirmed standard without reverting users or counters."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/opt/kingline-manager/app')
from node_manager.cover import certificate_lock
from node_manager.store import Store, atomic_json
from remna_api import RemnaAPI

os.umask(0o077)
root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
source = Path(json.loads((control / 'owned-target-migration.json').read_text())['rollback_directory'])
saved = {name: json.loads((source / (name + '.json')).read_text())
         for name in ['profile', 'hosts', 'owner-profile']}
store = Store(root)
with store.db() as db:
    assert not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone(), 'Install job active'
api = RemnaAPI()
state = json.loads((control / 'owner-profile.json').read_text())
profile = api.call('config-profiles/' + state['profile_uuid'])
hosts = [h for h in api.items('hosts', 'hosts') if h['inbound']['configProfileUuid'] == state['profile_uuid']]
assert {h['uuid'] for h in hosts} == {h['uuid'] for h in saved['hosts']}, 'Host inventory changed'
assert state['profile_uuid'] == saved['profile']['uuid']
assert state['user']['vlessUuid'] == saved['owner-profile']['user']['vlessUuid']
settings = json.loads((root / 'settings.json').read_text())
archive = control / ('before-owned-target-rollback-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'))
archive.mkdir(mode=0o700)
for name, value in [('profile', profile), ('hosts', hosts), ('owner-profile', state),
                    ('settings', settings), ('nodes', store.nodes())]:
    atomic_json(archive / (name + '.json'), value)
subprocess.run(['systemctl', 'disable', '--now', 'kingline-cover-maintenance.timer'], check=True)
subprocess.run(['systemctl', 'stop', 'kingline-cover-maintenance.service',
                'kingline-node-manager', 'kingline-smart-dns'], check=True)
try:
    with certificate_lock(root):
        api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': profile['name'],
                                              'config': saved['profile']['config']})
        for host in saved['hosts']:
            api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'sni': host.get('sni')})
        # Preserve current user details; only restore the protocol-related values.
        for key in ['sni', 'transport', 'fingerprint', 'reality_target', 'camouflage_updated_at']:
            if key in saved['owner-profile']:
                state[key] = saved['owner-profile'][key]
            else:
                state.pop(key, None)
        inbound = next(i for i in saved['profile']['config']['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
        reality = inbound['streamSettings']['realitySettings']
        state['reality_target'] = reality.get('target', reality.get('dest'))
        atomic_json(control / 'owner-profile.json', state)
        settings.pop('cover', None)
        atomic_json(root / 'settings.json', settings)
        (control / 'cover-policy.json').unlink(missing_ok=True)
        atomic_json(control / 'owned-target-rollback.json', {
            'restored_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source': str(source), 'archive': str(archive), 'sni': state['sni'],
            'target': state['reality_target'], 'profile_count': len(hosts),
            'credentials_preserved': True, 'verification_pending': True})
finally:
    subprocess.run(['systemctl', 'start', 'kingline-node-manager', 'kingline-smart-dns'], check=True)
print(json.dumps({'restored': True, 'sni': state['sni'], 'target': state['reality_target'],
                  'profiles': len(hosts), 'archive': str(archive)}), flush=True)
subprocess.run(['python3', '/opt/kingline-control/verify-vpn.py'], check=True, timeout=180)
report = json.loads((control / 'owned-target-rollback.json').read_text())
report['verification_pending'] = False
report['verified_from_lithuania'] = True
atomic_json(control / 'owned-target-rollback.json', report)
