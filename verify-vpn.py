#!/usr/bin/env python3
"""Run on the core. Real encrypted tunnels, public exit IPs and UDP DNS checks."""
import base64
import copy
import datetime
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

os.umask(0o077)
root = Path('/opt/kingline-control')
state = json.loads((root / 'owner-profile.json').read_text())
url = 'https://connect.kingline.online/' + state['user']['shortUuid']
request = urllib.request.Request(url, headers={'User-Agent': 'v2raytun/2.4.7'})
with urllib.request.urlopen(request, timeout=30) as response:
    data = response.read()
    headers = dict(response.headers)
try:
    text = base64.b64decode(data).decode()
except Exception:
    text = data.decode()
links = [line for line in text.splitlines() if line.startswith('vless://')]
inventory = json.loads((root / 'nodes.json').read_text())
published = [n for n in inventory if n.get('provisioned', True)]
assert len(links) == 1 + len(published), 'Expected smart profile plus every provisioned node'
(root / 'owner-subscription.txt').write_text(text)
config = {'log': {'loglevel': 'warning'}, 'inbounds': [], 'outbounds': [], 'routing': {'rules': []}}
profiles = []
for i, link in enumerate(links):
    parsed = urllib.parse.urlsplit(link)
    params = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
    entry = next((n for n in published if n['public_ip'] == parsed.hostname), None)
    connection = {**state, **(entry.get('manual_connection', {}) if entry else {})}
    assert parsed.port == connection.get('port', 443), 'Subscription endpoint port differs from selected revision'
    assert params['security'] == 'reality' and params['type'] in ['xhttp', 'tcp', 'raw']
    expected_transport = state.get('transport', 'xhttp')
    compatible = ['tcp', 'raw'] if expected_transport in ['tcp', 'raw'] else [expected_transport]
    assert params['type'] in compatible, 'Subscription still contains the previous transport'
    if connection.get('fingerprint'):
        assert params.get('fp') == connection['fingerprint'], 'Subscription fingerprint differs from the selected revision'
    assert params.get('sni') == connection['sni'], 'Subscription SNI differs from the selected revision'
    assert params.get('pbk') == state['reality']['publicKey'], 'Subscription REALITY key differs from the selected revision'
    assert params.get('sid') == state['short_id'], 'Subscription short ID differs from the selected revision'
    name, tag, port = urllib.parse.unquote(parsed.fragment), 'test-' + str(i), 18081 + i
    config['inbounds'].append({'tag': tag, 'listen': '127.0.0.1', 'port': port, 'protocol': 'socks',
                               'settings': {'auth': 'noauth', 'udp': True}})
    outbound = {'tag': tag, 'protocol': 'vless', 'settings': {'vnext': [{
        'address': parsed.hostname, 'port': parsed.port,
        'users': [{'id': parsed.username, 'encryption': 'none', 'flow': params.get('flow', '')}]}]},
        'streamSettings': {'network': params['type'], 'security': 'reality',
            'realitySettings': {'serverName': params['sni'], 'fingerprint': params.get('fp', 'chrome'),
                'publicKey': params['pbk'], 'shortId': params['sid']}}}
    if params['type'] == 'xhttp':
        outbound['streamSettings']['xhttpSettings'] = {'path': params.get('path', '/'), 'mode': params.get('mode', 'auto')}
        if 'extra' in params:
            outbound['streamSettings']['xhttpSettings']['extra'] = json.loads(params['extra'])
        if state.get('xhttp_extra'):
            assert params.get('mode') == state['mode']
            assert json.loads(params.get('extra', '{}')) == state['xhttp_extra'], 'Mobile settings missing from subscription'
    else:
        assert params.get('flow') == 'xtls-rprx-vision', 'Vision missing from generated subscription'
        assert 'extra' not in params and 'mode' not in params, 'XHTTP parameters remain in the TCP subscription'
    config['outbounds'].append(outbound)
    config['routing']['rules'].append({'type': 'field', 'inboundTag': [tag], 'outboundTag': tag})
    profiles.append({'name': name, 'address': parsed.hostname, 'port': port, 'transport': params['type'],
                     'flow': params.get('flow', ''), 'fingerprint': params.get('fp'),
                     'sni': params['sni'], 'server_port': parsed.port})
invalid = copy.deepcopy(config['outbounds'][1])
invalid['tag'] = 'unauthorized-probe'
invalid['settings']['vnext'][0]['users'][0]['id'] = str(uuid.uuid4())
config['outbounds'].append(invalid)
invalid_port = 18081 + len(links)
config['inbounds'].append({'tag': invalid['tag'], 'listen': '127.0.0.1', 'port': invalid_port,
                           'protocol': 'socks', 'settings': {'auth': 'noauth'}})
config['routing']['rules'].append({'type': 'field', 'inboundTag': [invalid['tag']], 'outboundTag': invalid['tag']})
file = root / 'probe-client.json'
file.write_text(json.dumps(config))
image = 'remnawave/node@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d'
client_binary = root / 'client-26.6.27/xray'
runtime = ['-v', str(client_binary) + ':/tmp/client-xray:ro', '--entrypoint', '/tmp/client-xray'] if client_binary.exists() else ['--entrypoint', '/usr/local/bin/xray']
base = ['docker', 'run', '--rm', '--network', 'host', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '-v', str(file) + ':/tmp/probe.json:ro',
        *runtime]
test = subprocess.run(base + [image, 'run', '-test', '-config', '/tmp/probe.json'],
                      capture_output=True, text=True)
assert test.returncode == 0, 'Xray rejected generated client configuration'

def udp_dns(port):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as tcp:
        tcp.sendall(b'\x05\x01\x00')
        assert tcp.recv(2) == b'\x05\x00'
        tcp.sendall(b'\x05\x03\x00\x01' + b'\x00' * 6)
        reply = tcp.recv(32)
        assert reply[1] == 0 and reply[3] == 1, 'SOCKS UDP association failed'
        udp_port = int.from_bytes(reply[8:10], 'big')
        transaction = secrets.randbelow(65536)
        query = struct.pack('!HHHHHH', transaction, 0x0100, 1, 0, 0, 0)
        query += b'\x07example\x03com\x00\x00\x01\x00\x01'
        packet = b'\x00\x00\x00\x01' + socket.inet_aton('9.9.9.9') + struct.pack('!H', 53) + query
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(10)
            udp.sendto(packet, ('127.0.0.1', udp_port))
            reply = udp.recv(4096)
        assert reply[:4] == b'\x00\x00\x00\x01'
        response = reply[10:]
        received, flags = struct.unpack('!HH', response[:4])
        assert received == transaction and flags & 0x8000 and flags & 0x000F == 0

subprocess.run(['docker', 'rm', '-f', 'kingline-probe'], stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL)
subprocess.run(base[:2] + ['-d', '--name', 'kingline-probe'] + base[2:]
               + [image, 'run', '-config', '/tmp/probe.json'],
               stdout=subprocess.DEVNULL, check=True)
report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'client_core': '26.6.27' if client_binary.exists() else '26.7.28',
          'source': 'Lithuania control server; not a measurement from the user mobile network',
          'subscription_profile_count': len(links), 'checks': []}
try:
    for _ in range(30):
        try:
            with socket.create_connection(('127.0.0.1', 18081), timeout=1):
                break
        except OSError:
            time.sleep(0.2)
    for profile in profiles:
        result = subprocess.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '25',
            '--proxy', 'socks5h://127.0.0.1:' + str(profile['port']),
            '--write-out', '\n%{time_total}', 'https://api.ipify.org'], capture_output=True, text=True)
        if result.returncode != 0:
            print('Curl diagnostic:', result.stderr.strip(), flush=True)
            logs = subprocess.check_output(['docker', 'logs', 'kingline-probe'], stderr=subprocess.STDOUT).decode()
            for item in state['user'].values():
                if isinstance(item, str) and len(item) > 15:
                    logs = logs.replace(item, '[redacted]')
            print('Probe diagnostic:', logs[-3500:], flush=True)
            raise RuntimeError('Encrypted tunnel failed: ' + profile['name'])
        exit_ip, elapsed = result.stdout.strip().splitlines()
        expected = [n['public_ip'] for n in published] if profile['address'].startswith('auto.') else [profile['address']]
        assert exit_ip in expected, 'Unexpected public exit address'
        udp_dns(profile['port'])
        report['checks'].append({'name': profile['name'], 'exit_ip': exit_ip, 'transport': profile['transport'],
                                'sni': profile['sni'], 'server_port': profile['server_port'],
                                'flow': profile['flow'], 'fingerprint': profile['fingerprint'],
                                'https_request_seconds': float(elapsed), 'udp_dns': 'passed'})
        print(json.dumps(report['checks'][-1], ensure_ascii=False), flush=True)
    for name, port, destination in [('invalid_user_rejected', invalid_port, 'https://example.com'),
                                     ('management_network_blocked', 18082, 'http://10.77.0.2:2222')]:
        denied = subprocess.run(['curl', '-sS', '--max-time', '6', '--proxy', 'socks5h://127.0.0.1:' + str(port),
                                 destination], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert denied.returncode != 0, name + ': request unexpectedly succeeded'
        report[name] = True
    print('Unauthorized user rejected; access from VPN to private management network blocked', flush=True)
    (root / 'verification-vpn.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
finally:
    subprocess.run(['docker', 'rm', '-f', 'kingline-probe'], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
