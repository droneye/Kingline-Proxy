#!/usr/bin/env python3
"""Validate AWS mobile settings in private canaries before changing public hosts."""
import copy
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, '/opt/kingline-manager/app')
from node_manager.probe import verify_tunnel
from node_manager.ssh import SSH
from node_manager.store import atomic_json
from remna_api import RemnaAPI
from mobile_variant import candidate_config, TAG, SNI, FINGERPRINT, PORT

root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
state = json.loads((control / 'owner-profile.json').read_text())
settings = json.loads((root / 'settings.json').read_text())
profile = RemnaAPI(root / 'panel.token').call('config-profiles/' + state['profile_uuid'])
candidate = candidate_config(profile['config'])
atomic_json(control / 'candidate-mobile-aws.json', candidate)
prepare = '''import json,pathlib,socket,subprocess
p=json.load(__import__('sys').stdin)
with socket.socket() as s:s.bind(('0.0.0.0',36847))
f=pathlib.Path('/opt/kingline-node/mobile-canary.json'); f.write_text(json.dumps(p['config'])); f.chmod(0o600)
def run(args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
run(['nft','add','rule','inet','kingline_filter','input','iifname','wg-control','ip','saddr','10.77.0.1','tcp','dport','1443','accept','comment','kingline-mobile-canary'])
run(['docker','run','-d','--rm','--name','kingline-mobile-canary','--network','host','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','-v',str(f)+':/tmp/config.json:ro','--entrypoint','/usr/local/bin/xray',p['image'],'run','-config','/tmp/config.json'])
print('{}')'''
cleanup = '''import json,pathlib,subprocess
subprocess.run(['docker','rm','-f','kingline-mobile-canary'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
rules=json.loads(subprocess.check_output(['nft','-j','-a','list','chain','inet','kingline_filter','input']))
for item in rules['nftables']:
 r=item.get('rule',{})
 if r.get('comment')=='kingline-mobile-canary':subprocess.run(['nft','delete','rule','inet','kingline_filter','input','handle',str(r['handle'])],check=True)
pathlib.Path('/opt/kingline-node/mobile-canary.json').unlink(missing_ok=True)
print('{}')'''
results = []
for node in json.loads((control / 'nodes.json').read_text()):
    if node['public_ip'] not in ['198.51.100.31', '198.51.100.33']:
        continue
    config = copy.deepcopy(candidate)
    inbound = next(i for i in config['inbounds'] if i['tag'] == TAG)
    inbound.update(listen=node['management_ip'], port=1443)
    inbound['settings']['clients'] = [{'id': state['user']['vlessUuid'], 'flow': 'xtls-rprx-vision'}]
    config['inbounds'] = [inbound]
    ssh = SSH(root, {'ip': node['public_ip'], 'ssh_port': 22, 'host_key_sha256': node['host_key_sha256']})
    try:
        ssh.python(cleanup)
        ssh.python(prepare, {'config': config, 'image': settings['node_image']})
        time.sleep(1)
        for attempt in range(2):
            result = verify_tunnel(control, node['public_ip'],
                state_override={**state, 'sni': SNI, 'fingerprint': FINGERPRINT},
                connect_address=node['management_ip'], server_port=1443,
                exit_urls=('https://api.ipify.org', 'https://ipv4.icanhazip.com'))
            results.append({'node': node['name'], 'sni': SNI, 'fingerprint': FINGERPRINT,
                            'production_port': PORT, 'warm': attempt > 0, **result})
            print(json.dumps(results[-1]), flush=True)
    finally:
        ssh.python(cleanup)
        ssh.close()
atomic_json(control / 'verification-mobile-canary.json', results)
