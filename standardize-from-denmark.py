#!/usr/bin/env python3
"""Make every published location match the owner-confirmed Danish connection."""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/opt/kingline-manager/app')
from node_manager.cover import certificate_lock
from node_manager.mobile import REMOVE_MOBILE_FIREWALL
from node_manager.ssh import SSH
from node_manager.store import Store, atomic_json
from remna_api import RemnaAPI

os.umask(0o077)
root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
store = Store(root)
with store.db() as db:
    assert not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone()
nodes = store.nodes()
danish = next(n for n in nodes if n['public_ip'] == '198.51.100.32')
api = RemnaAPI()
state = json.loads((control / 'owner-profile.json').read_text())
profile = api.call('config-profiles/' + state['profile_uuid'])
hosts = [h for h in api.items('hosts', 'hosts') if h['inbound']['configProfileUuid'] == profile['uuid']]
assert len(hosts) == len(nodes) + 1
reference = next(h for h in hosts if h['uuid'] == danish['host_uuid'])
assert reference['inbound']['configProfileInboundUuid'] == state['inbound_uuid']
main = next(i for i in profile['config']['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
assert reference['port'] == main['port'] == 443
assert reference['sni'] in main['streamSettings']['realitySettings']['serverNames']
assert reference['fingerprint'] == 'firefox'
mobile = next((i for i in profile['inbounds'] if i['tag'] == 'KINGLINE_MOBILE_AWS'), None)
assert all(i['tag'] in ['KINGLINE_XHTTP_REALITY', 'KINGLINE_MOBILE_AWS'] for i in profile['config']['inbounds'])
config = copy.deepcopy(profile['config'])
config['inbounds'] = [copy.deepcopy(main)]
config['outbounds'] = [o for o in config['outbounds'] if o.get('tag') != 'MOBILE_DIRECT']
config['routing']['rules'] = [r for r in config['routing']['rules']
                            if r.get('outboundTag') != 'MOBILE_DIRECT']
stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
archive = control / ('before-danish-standard-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'))
archive.mkdir(mode=0o700)
for name, value in [('profile', profile), ('hosts', hosts), ('nodes', nodes), ('owner-profile', state)]:
    atomic_json(archive / (name + '.json'), value)
candidate = control / 'candidate-danish-standard.json'
atomic_json(candidate, config)
settings = json.loads((root / 'settings.json').read_text())
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '-v', str(candidate) + ':/tmp/config.json:ro', '--entrypoint', '/usr/local/bin/xray',
    settings['node_image'], 'run', '-test', '-config', '/tmp/config.json'],
    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(['systemctl', 'stop', 'kingline-node-manager', 'kingline-smart-dns',
                'kingline-tunnel-health.timer', 'kingline-tunnel-health.service'], check=True)
try:
    with certificate_lock(root):
        # Withdraw public references before deleting the alternative inbound.
        for host in hosts:
            api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'port': reference['port'],
                'sni': reference['sni'], 'fingerprint': reference['fingerprint'],
                'inbound': reference['inbound']})
        for node in nodes:
            api.call('nodes', 'PATCH', {'uuid': node['uuid'], 'configProfile': {
                'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [state['inbound_uuid']]}})
        if mobile:
            for squad in api.items('internal-squads', 'internalSquads'):
                ids = [i['uuid'] for i in squad['inbounds']]
                if mobile['uuid'] in ids:
                    api.call('internal-squads', 'PATCH', {'uuid': squad['uuid'],
                                                        'inbounds': [i for i in ids if i != mobile['uuid']]})
        api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': profile['name'], 'config': config})
        for node in nodes:
            if node.get('manual_connection'):
                ssh = SSH(root, {'ip': node['public_ip'], 'ssh_port': node.get('ssh_port', 22),
                                 'host_key_sha256': node['host_key_sha256']})
                try:
                    ssh.run(REMOVE_MOBILE_FIREWALL, timeout=30)
                finally:
                    ssh.close()
            updated = dict(node)
            updated.pop('manual_connection', None)
            # User reports Danish connectivity; keep other manual locations usable.
            updated['maintenance'] = node['uuid'] != danish['uuid']
            if updated['maintenance']:
                updated['routing_hold_reason'] = 'Owner reports mobile connectivity best via Denmark; manual profile remains available'
            else:
                updated.pop('routing_hold_reason', None)
            store.save_node(updated)
        state.update(sni=reference['sni'], fingerprint=reference['fingerprint'], transport='tcp',
            reality_target=main['streamSettings']['realitySettings'].get('target', main['streamSettings']['realitySettings'].get('dest')))
        atomic_json(control / 'owner-profile.json', state)
        atomic_json(control / 'nodes.json', store.nodes())
        report = {'applied_at': stamp, 'archive': str(archive), 'reference': danish['name'],
            'sni': reference['sni'], 'port': reference['port'], 'fingerprint': reference['fingerprint'],
            'target': state['reality_target'], 'profile_count': len(hosts), 'smart_temporarily': 'Denmark',
            'server_config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            'old_credentials_preserved': True, 'russian_network_test_pending': True}
        atomic_json(control / 'danish-standard.json', report)
        mobile_report = control / 'mobile-variant.json'
        if mobile_report.exists():
            retired = json.loads(mobile_report.read_text())
            retired.update(active=False, retired_at=stamp, reason='Owner requested identical Danish settings for all profiles')
            atomic_json(mobile_report, retired)
finally:
    subprocess.run(['systemctl', 'start', 'kingline-node-manager', 'kingline-smart-dns',
                    'kingline-tunnel-health.timer'], check=True)
time.sleep(3)
subprocess.run(['python3', str(control / 'verify-vpn.py')], check=True, timeout=180)
subprocess.run(['systemctl', 'start', 'kingline-tunnel-health.service'], check=True)
print(json.dumps(report), flush=True)
