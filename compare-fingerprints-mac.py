#!/usr/bin/env python3
"""Bounded tests through local SOCKS listeners; leaves macOS routing/proxy settings untouched."""
import concurrent.futures
import copy
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import time

os.umask(0o077)
root = Path(__file__).resolve().parent
private = root / 'private'
binary = private / 'tools/xray-26.6.27/xray'
source = json.loads((private / 'probe-mac-source.json').read_text())
config = {'log': {'loglevel': 'warning'}, 'inbounds': [], 'outbounds': [], 'routing': {'rules': []}}
cases = []
for fingerprint in ['chrome', 'firefox']:
    for index, name in enumerate(['smart', 'Germany', 'Denmark']):
        number = len(cases)
        port, tag = 24880 + number, 'compare-' + str(number)
        outbound = copy.deepcopy(source['outbounds'][index])
        outbound['tag'] = tag
        outbound['streamSettings']['realitySettings']['fingerprint'] = fingerprint
        outbound['streamSettings']['sockopt'] = {'interface': 'en0'}
        config['inbounds'].append({'tag': tag, 'listen': '127.0.0.1', 'port': port,
            'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': True}})
        config['outbounds'].append(outbound)
        config['routing']['rules'].append({'type': 'field', 'inboundTag': [tag], 'outboundTag': tag})
        cases.append({'name': name, 'fingerprint': fingerprint, 'port': port})
file = private / 'probe-mac-fingerprints.json'
file.write_text(json.dumps(config))
test = subprocess.run([str(binary), 'run', '-test', '-config', str(file)], capture_output=True)
assert test.returncode == 0, 'Test configuration rejected'
report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'source': 'User Mac, physical interface en0; not the iPhone mobile network',
          'client_core': '26.6.27', 'checks': []}
try:
    report['smart_dns_answers'] = sorted({item[4][0] for item in socket.getaddrinfo(
        'auto.edge.kingline.online', 443, socket.AF_INET, socket.SOCK_STREAM)})
except OSError:
    report['smart_dns_answers'] = []
log = (private / 'probe-mac-fingerprints.log').open('wb')
process = subprocess.Popen([str(binary), 'run', '-config', str(file)], stdout=log, stderr=log)

def check(case):
    base = ['/usr/bin/curl', '-sS', '--fail', '--connect-timeout', '8', '--max-time', '25',
            '--proxy', 'socks5h://127.0.0.1:' + str(case['port']), '--noproxy', '']
    ip = subprocess.run(base + ['https://api.ipify.org'], capture_output=True, text=True)
    row = {k: v for k, v in case.items() if k != 'port'}
    row['ip_check_exit_code'] = ip.returncode
    row['exit_ip'] = ip.stdout.strip() if ip.stdout.strip() in ['198.51.100.31', '198.51.100.32'] else None
    row['ip_error'] = ip.stderr.strip()[:250]
    transfer = subprocess.run(base + ['--output', '/dev/null', '--write-out', '%{json}',
        'https://speed.cloudflare.com/__down?bytes=1048576'], capture_output=True, text=True)
    metrics = json.loads(transfer.stdout or '{}')
    row.update({'download_exit_code': transfer.returncode, 'http_code': metrics.get('http_code'),
                'bytes': metrics.get('size_download'), 'seconds': metrics.get('time_total'),
                'download_error': transfer.stderr.strip()[:250]})
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row

try:
    for _ in range(40):
        assert process.poll() is None, 'Local test process exited'
        try:
            with socket.create_connection(('127.0.0.1', cases[0]['port']), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        report['checks'] = list(pool.map(check, cases))
    (root / 'verification-fingerprints-mac.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    log.close()
