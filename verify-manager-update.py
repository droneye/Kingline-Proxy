#!/usr/bin/env python3
"""Read-only final audit on the control server; never output credentials."""
import collections
import datetime
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

sys.path.insert(0, '/opt/kingline-manager/app')
from node_manager.ssh import SSH
from remna_api import RemnaAPI

root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
old_ip, new_ip = '198.51.100.36', '198.51.100.33'
old_uuid = 'f2bcb70d-c27c-4dd1-89a9-233b23726f7d'
headers = {'Authorization': 'Bearer ' + (root / 'admin.token').read_text().strip()}

def manager(path):
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8090' + path,
                                                      headers=headers), timeout=30) as response:
        return json.load(response)

report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
inventory = manager('/v1/nodes')
nodes = inventory['nodes']
expected_ips = {'198.51.100.31', '198.51.100.32', new_ip}
assert {n['public_ip'] for n in nodes} == expected_ips
assert all(n['removable'] and n['managed_ssh'] and n['provisioned'] for n in nodes)
report['nodes'] = [{k: n[k] for k in ['uuid', 'name', 'display_name', 'flag_code', 'public_ip',
                                    'quota_bytes', 'speed_mbps', 'removable']} for n in nodes]
new = next(n for n in nodes if n['public_ip'] == new_ip)
assert new['quota_bytes'] == 2 * 10**12 and new['speed_mbps'] == 25000
assert new['label'] == '🇹🇷 Турция' and new['flag_code'] == 'TR'
job = manager('/v1/jobs/' + new['job_id'])
assert job['state'] == 'ready'
report['new_node_job'] = {'id': new['job_id'], 'state': job['state']}
removed = manager('/v1/removals/' + old_uuid)
assert removed['state'] == 'removed' and removed['ip'] == old_ip
report['old_node_removal'] = removed
schema = manager('/v1/openapi.json')
assert 'delete' in schema['paths']['/v1/nodes/{node_uuid}']
assert {'display_name', 'flag_code'} <= set(schema['components']['schemas']['AddNode']['properties'])
report['delete_and_display_fields_in_deployed_api'] = True

api = RemnaAPI(root / 'panel.token')
panel_nodes, hosts = api.items('nodes', 'nodes'), api.items('hosts', 'hosts')
assert {n['uuid'] for n in panel_nodes} == {n['uuid'] for n in nodes}
assert all(n['isConnected'] for n in panel_nodes)
assert old_ip not in json.dumps(hosts) and old_uuid not in json.dumps(hosts)
smart = next(h for h in hosts if h['address'] == 'auto.edge.kingline.online')
assert {n if isinstance(n, str) else n['uuid'] for n in smart['nodes']} == {n['uuid'] for n in nodes}
assert any(h['address'] == new_ip and h['remark'] == '🇹🇷 Турция' for h in hosts)
report['panel_and_smart_host_match_inventory'] = True

report['backend_ssh'] = []
public_key = (root / 'id_ed25519.pub').read_text().split()[1]
for node in nodes:
    ssh = SSH(root, {'ip': node['public_ip'], 'ssh_port': node.get('ssh_port', 22),
                     'host_key_sha256': node['host_key_sha256']})
    try:
        assert ssh.run('id -u').strip() == '0'
        authorized = ssh.run('cat /root/.ssh/authorized_keys')
        line = next(line for line in authorized.splitlines() if public_key in line)
        assert line.startswith('from="198.51.100.10') and 'no-port-forwarding' in line
        report['backend_ssh'].append({'ip': node['public_ip'], 'root_key_login': True,
                                      'restricted_to_control_server': True})
        if node['public_ip'] == new_ip:
            effective = dict(line.split(None, 1) for line in ssh.run('/usr/sbin/sshd -T').splitlines())
            assert effective['passwordauthentication'] == effective['kbdinteractiveauthentication'] == 'no'
            assert effective['permitrootlogin'] in ['prohibit-password', 'without-password']
            report['new_node_ssh_key_only'] = True
    finally:
        ssh.close()

old = SSH(root, {'ip': old_ip, 'ssh_port': 22})
try:
    state = json.loads(old.python('''import json,pathlib,subprocess
def run(*args): return subprocess.run(args,capture_output=True,text=True).stdout.strip()
print(json.dumps({'containers':run('docker','ps','-aq','--filter','name=^/remnanode$'),
'listeners443':run('ss','-H','-lnt','sport = :443'),
'wireguard':run('systemctl','is-active','wg-quick@wg-control'),
'vpn_files':any(pathlib.Path(p).exists() for p in ['/opt/remnanode/.env','/opt/remnanode/docker-compose.yml'])}))'''))
    assert not state['containers'] and not state['listeners443'] and not state['vpn_files']
    assert state['wireguard'] != 'active'
    report['old_server_vpn_removed'] = True
finally:
    old.close()
for path in ['/etc/wireguard/wg-control.conf', '/etc/kingline-firewall.nft']:
    text = Path(path).read_text()
    assert old_ip not in text and new_ip in text
report['old_management_peer_removed'] = True
assert {n['ip'] for n in inventory['routing']['nodes']} == expected_ips
assert all(n['weight'] > 0 for n in inventory['routing']['nodes'])
report['routing'] = inventory['routing']
report['dns_replicas'] = []
for server in ['198.51.100.10', '198.51.100.31', '198.51.100.32']:
    answers = []
    for _ in range(24):
        output = subprocess.check_output(['dig', '@' + server, 'auto.edge.kingline.online', 'A',
                                          '+short', '+time=2', '+tries=1'], text=True).strip()
        assert output in expected_ips, 'Unexpected DNS answer'
        answers.append(output)
    assert new_ip in answers
    report['dns_replicas'].append({'server': server, 'samples': 24,
                                   'answers': dict(collections.Counter(answers)), 'old_ip_absent': True})
(control / 'verification-manager-update.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
