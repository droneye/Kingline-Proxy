#!/usr/bin/env python3
"""Test the owned target using temporary, management-only Xray listeners."""
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

root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
state = json.loads((control / 'owner-profile.json').read_text())
settings = json.loads((root / 'settings.json').read_text())
profile = RemnaAPI(root / 'panel.token').call('config-profiles/' + state['profile_uuid'])
inbound = copy.deepcopy(next(i for i in profile['config']['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY'))
inbound['port'] = 1443
inbound['settings']['clients'] = [{'id': state['user']['vlessUuid'], 'flow': 'xtls-rprx-vision'}]
reality = inbound['streamSettings']['realitySettings']
reality.pop('dest', None)
reality['target'] = '127.0.0.1:8443'
reality['serverNames'] = ['edge.kingline.online', state['sni']]
config = copy.deepcopy(profile['config'])
config['inbounds'] = [inbound]
if '--internal-dns' in sys.argv:
    config['dns'] = {'servers': ['1.1.1.1', '9.9.9.9'], 'queryStrategy': 'UseIPv4'}
    for outbound in config['outbounds']:
        if outbound['protocol'] == 'freedom':
            outbound.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'
prepare = '''import json,pathlib,subprocess
p=json.load(__import__('sys').stdin)
f=pathlib.Path('/opt/kingline-node/cover-canary.json'); f.write_text(json.dumps(p['config'])); f.chmod(0o600)
def run(args): subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
run(['nft','add','rule','inet','kingline_filter','input','iifname','wg-control','ip','saddr','10.77.0.1','tcp','dport','1443','accept','comment','kingline-cover-canary'])
run(['docker','run','-d','--rm','--name','kingline-cover-canary','--network','host','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','-v',str(f)+':/tmp/config.json:ro','--entrypoint','/usr/local/bin/xray',p['image'],'run','-config','/tmp/config.json'])
print('{}')'''
cleanup = '''import json,pathlib,subprocess
subprocess.run(['docker','rm','-f','kingline-cover-canary'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
rules=json.loads(subprocess.check_output(['nft','-j','-a','list','chain','inet','kingline_filter','input']))
for item in rules['nftables']:
 r=item.get('rule',{})
 if r.get('comment')=='kingline-cover-canary':subprocess.run(['nft','delete','rule','inet','kingline_filter','input','handle',str(r['handle'])],check=True)
pathlib.Path('/opt/kingline-node/cover-canary.json').unlink(missing_ok=True)
print('{}')'''
results = []
for n in json.loads((control / 'nodes.json').read_text()):
    ssh = SSH(root, {'ip': n['public_ip'], 'ssh_port': 22, 'host_key_sha256': n['host_key_sha256']})
    try:
        ssh.python(cleanup)
        inbound['listen'] = n['management_ip']
        ssh.python(prepare, {'config': config, 'image': settings['node_image']})
        time.sleep(1)
        for attempt, sni in enumerate(['edge.kingline.online', 'edge.kingline.online']):
            temporary = {**state, 'sni': sni}
            result = verify_tunnel(control, n['public_ip'], state_override=temporary,
                                   connect_address=n['management_ip'], server_port=1443)
            results.append({'node': n['name'], 'sni': sni, 'warm_connection': attempt > 0, **result})
            atomic_json(control / ('verification-owned-target-canary-internal-dns.json' if '--internal-dns' in sys.argv
                                   else 'verification-owned-target-canary.json'), results)
            print(json.dumps(results[-1]), flush=True)
    finally:
        ssh.python(cleanup)
        ssh.close()
atomic_json(control / ('verification-owned-target-canary-internal-dns.json' if '--internal-dns' in sys.argv
                       else 'verification-owned-target-canary.json'), results)
