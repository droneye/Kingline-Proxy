#!/usr/bin/env python3
"""Read-only server inventory; emits no keys, passwords or environment variables."""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

role = sys.argv[1]
def run(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()

report = {'role': role, 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'kernel': run('uname', '-r'), 'services': {}, 'containers': [],
          'reboot_required': Path('/var/run/reboot-required').exists()}
for name in ['docker', 'kingline-firewall', 'wg-quick@wg-control'] + (['fail2ban'] if role != 'core' else []):
    report['services'][name] = {'active': run('systemctl', 'is-active', name),
                                'enabled': run('systemctl', 'is-enabled', name)}
    assert report['services'][name] == {'active': 'active', 'enabled': 'enabled'}, name + ' not ready'
report['failed_units'] = run('systemctl', '--failed', '--no-legend', '--no-pager')
assert not report['failed_units'], 'Failed system service remains'
assert not report['reboot_required'], 'System reboot still required'
ids = run('docker', 'ps', '-aq').split()
for container_id in ids:
    value = json.loads(run('docker', 'inspect', container_id))[0]
    item = {'name': value['Name'].lstrip('/'), 'status': value['State']['Status'],
            'health': value['State'].get('Health', {}).get('Status', 'not configured'),
            'restart_policy': value['HostConfig']['RestartPolicy']['Name'],
            'image': value['Config']['Image']}
    assert item['status'] == 'running' and item['health'] != 'unhealthy', item['name'] + ' not ready'
    report['containers'].append(item)
report['tcp_listeners'] = run('ss', '-H', '-lnt')
report['firewall'] = run('nft', 'list', 'table', 'inet', 'kingline_filter')
assert 'policy drop' in report['firewall']
now = int(datetime.datetime.now().timestamp())
report['wireguard_handshake_age_seconds'] = [now - int(line.split()[1])
    for line in run('wg', 'show', 'wg-control', 'latest-handshakes').splitlines()]
assert all(0 <= age < 180 for age in report['wireguard_handshake_age_seconds'])
if role != 'core':
    report['old_xui_files_present'] = [p for p in ['/usr/local/x-ui', '/etc/x-ui', '/usr/bin/x-ui',
        '/etc/systemd/system/x-ui.service'] if Path(p).exists()]
    report['old_xui_service'] = run('systemctl', 'is-active', 'x-ui')
    assert not report['old_xui_files_present'] and report['old_xui_service'] != 'active'
    report['ssh_protection'] = run('fail2ban-client', 'status', 'sshd')
    assert 'sshd' in report['ssh_protection']
    if role == 'de':
        report['firewalld'] = run('systemctl', 'is-enabled', 'firewalld')
        assert report['firewalld'] == 'masked'
else:
    root = Path('/opt/remnawave')
    credentials = json.loads((root / 'admin-credentials.json').read_text())
    headers = {'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https',
               'X-Forwarded-For': '127.0.0.1', 'X-Remnawave-Client-Type': 'browser'}
    login = urllib.request.Request('http://127.0.0.1:3000/api/auth/login', headers=headers,
        data=json.dumps({k: credentials[k] for k in ['username', 'password']}).encode())
    with urllib.request.urlopen(login, timeout=20) as response:
        headers['Authorization'] = 'Bearer ' + json.load(response)['response']['accessToken']
    req = urllib.request.Request('http://127.0.0.1:3000/api/nodes', headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        nodes = json.load(response)['response']
    if isinstance(nodes, dict):
        nodes = nodes.get('nodes', nodes.get('items', []))
    report['panel_nodes'] = [{k: node.get(k) for k in ['name', 'address', 'isConnected',
        'lastStatusMessage', 'xrayVersion', 'nodeVersion', 'xrayUptime']} for node in nodes]
    inventory = json.loads(Path('/opt/kingline-control/nodes.json').read_text())
    expected = {node['uuid'] for node in inventory if node.get('provisioned', True)}
    selected = [node for node in nodes if node['uuid'] in expected]
    assert len(selected) == len(expected) and all(node['isConnected'] for node in selected), 'Nodes not connected'
    report['subscription_test'] = json.loads((root / 'verification-subscription.json').read_text())
print(json.dumps(report, ensure_ascii=False, indent=2))
