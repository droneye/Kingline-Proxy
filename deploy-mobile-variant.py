#!/usr/bin/env python3
"""Publish the proven AWS variant only to the two affected manual locations."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/opt/kingline-manager/app')
from node_manager.cover import certificate_lock
from node_manager.mobile import REMOVE_MOBILE_FIREWALL
from node_manager.probe import verify_tunnel
from node_manager.ssh import SSH
from node_manager.store import Store, atomic_json
from remna_api import RemnaAPI
from mobile_variant import candidate_config, TAG, SNI, PORT, FINGERPRINT

os.umask(0o077)
root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
state = json.loads((control / 'owner-profile.json').read_text())
store = Store(root)
with store.db() as db:
    assert not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone()
nodes = [n for n in store.nodes() if n['public_ip'] in ['198.51.100.31', '198.51.100.33']]
assert len(nodes) == 2 and all(not n.get('manual_connection') for n in nodes)
proof = json.loads((control / 'verification-mobile-canary.json').read_text())
assert all(any(t['exit_ip'] == n['public_ip'] and t['https'] and t['udp_dns']
               and t['sni'] == SNI and t['fingerprint'] == FINGERPRINT for t in proof) for n in nodes)
api = RemnaAPI()
profile = api.call('config-profiles/' + state['profile_uuid'])
candidate = candidate_config(profile['config'])
hosts = [h for h in api.items('hosts', 'hosts') if h['uuid'] in {n['host_uuid'] for n in nodes}]
assert len(hosts) == 2
squad = next(s for s in api.items('internal-squads', 'internalSquads') if s['name'] == 'Kingline Personal')
squad_ids = [i['uuid'] for i in squad['inbounds']]
assert state['inbound_uuid'] in squad_ids
panel_nodes = [api.call('nodes/' + n['uuid']) for n in nodes]
archive = control / ('before-mobile-aws-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'))
archive.mkdir(mode=0o700)
for name, value in [('profile', profile), ('hosts', hosts), ('nodes', nodes), ('panel-nodes', panel_nodes),
                    ('squad', {'uuid': squad['uuid'], 'inbounds': squad_ids})]:
    atomic_json(archive / (name + '.json'), value)
path = control / 'candidate-mobile-aws.json'
atomic_json(path, candidate)
settings = json.loads((root / 'settings.json').read_text())
assert not settings.get('enforce_uniform_profiles'), 'Uniform profile policy blocks split mobile variants'
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '-v', str(path) + ':/tmp/config.json:ro', '--entrypoint', '/usr/local/bin/xray',
    settings['node_image'], 'run', '-test', '-config', '/tmp/config.json'], check=True,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
firewall = '''import pathlib,re,subprocess
p=pathlib.Path('/etc/kingline-firewall.nft'); old=p.read_text()
assert 'kingline mobile 36847' not in old
new=re.sub(r'(?m)^(\\s*)ct state invalid drop$',lambda m:m.group(0)+'\\n  tcp dport 36847 accept comment "kingline mobile 36847"',old,count=1)
assert new!=old
p.write_text(new)
try:subprocess.run(['/usr/local/sbin/kingline-firewall'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
except Exception:p.write_text(old);raise
print('{}')'''
subprocess.run(['systemctl', 'stop', 'kingline-node-manager', 'kingline-tunnel-health.timer',
                'kingline-tunnel-health.service'], check=True)
opened = []
try:
    with certificate_lock(root):
        api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': profile['name'], 'config': candidate})
        current = api.call('config-profiles/' + profile['uuid'])
        mobile_id = next(i['uuid'] for i in current['inbounds'] if i['tag'] == TAG)
        assert next(i['uuid'] for i in current['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY') == state['inbound_uuid']
        api.call('internal-squads', 'PATCH', {'uuid': squad['uuid'], 'inbounds': squad_ids + [mobile_id]})
        for node in nodes:
            ssh = SSH(root, {'ip': node['public_ip'], 'ssh_port': 22, 'host_key_sha256': node['host_key_sha256']})
            try:
                (archive / (node['name'] + '-firewall.nft')).write_text(ssh.run('cat /etc/kingline-firewall.nft'))
                ssh.python(firewall)
                opened.append(node)
            finally:
                ssh.close()
            api.call('nodes', 'PATCH', {'uuid': node['uuid'], 'configProfile': {
                'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [state['inbound_uuid'], mobile_id]}})
        time.sleep(3)
        for node in nodes:
            result = verify_tunnel(control, node['public_ip'],
                state_override={**state, 'sni': SNI, 'fingerprint': FINGERPRINT}, server_port=PORT,
                exit_urls=('https://api.ipify.org', 'https://ipv4.icanhazip.com'))
            assert result['https'] and result['udp_dns']
            api.call('hosts', 'PATCH', {'uuid': node['host_uuid'], 'port': PORT,
                'sni': SNI, 'fingerprint': FINGERPRINT, 'inbound': {
                    'configProfileUuid': profile['uuid'], 'configProfileInboundUuid': mobile_id}})
            node = dict(node)
            node['manual_connection'] = {'port': PORT, 'sni': SNI, 'fingerprint': FINGERPRINT,
                'target': SNI + ':443', 'inbound_uuid': mobile_id}
            store.save_node(node)
        atomic_json(control / 'nodes.json', store.nodes())
        subprocess.run(['python3', str(control / 'verify-vpn.py')], check=True, timeout=180)
        atomic_json(control / 'mobile-variant.json', {'active': True, 'applied_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'archive': str(archive), 'nodes': [n['public_ip'] for n in nodes], 'sni': SNI,
            'port': PORT, 'fingerprint': FINGERPRINT, 'inbound_uuid': mobile_id,
            'old_keys_preserved': True, 'russian_mobile_verification': 'pending'})
except Exception:
    for host in hosts:
        api.call('hosts', 'PATCH', {k: host[k] for k in ['uuid', 'port', 'sni', 'fingerprint', 'inbound']})
    for node in nodes:
        api.call('nodes', 'PATCH', {'uuid': node['uuid'], 'configProfile': {
            'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [state['inbound_uuid']]}})
        store.save_node(node)
    api.call('internal-squads', 'PATCH', {'uuid': squad['uuid'], 'inbounds': squad_ids})
    api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': profile['name'], 'config': profile['config']})
    atomic_json(control / 'nodes.json', store.nodes())
    for node in opened:
        ssh = SSH(root, {'ip': node['public_ip'], 'ssh_port': 22, 'host_key_sha256': node['host_key_sha256']})
        try:
            ssh.run(REMOVE_MOBILE_FIREWALL)
        finally:
            ssh.close()
    raise
finally:
    subprocess.run(['systemctl', 'start', 'kingline-node-manager', 'kingline-tunnel-health.timer'], check=True)
print('AWS mobile variant published to Germany and Turkey; four profiles verified', flush=True)
