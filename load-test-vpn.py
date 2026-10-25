#!/usr/bin/env python3
"""Run on core after verify-vpn.py. Bounded load between our own servers only."""
import concurrent.futures
import datetime
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import threading
import time

os.umask(0o077)
root = Path('/opt/kingline-control')
inventory = json.loads((root / 'nodes.json').read_text())
allowed_ips = {str(ipaddress.IPv4Address(n['public_ip'])) for n in inventory if n.get('provisioned', True)}
verified = json.loads((root / 'verification-vpn.json').read_text())
profile_names = [row['name'] for row in verified['checks']]
assert len(profile_names) == 1 + len(allowed_ips)
profile_ports = [18081 + index for index in range(len(profile_names))]
label = sys.argv[1] if len(sys.argv) > 1 else 'current'
soak = label.endswith('-soak')
assert re.fullmatch('[a-z0-9-]+', label)
image = 'remnawave/node@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d'
cert, key = root / 'probe-load.crt', root / 'probe-load.key'
subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-subj', '/CN=Kingline internal test', '-addext', 'subjectAltName=IP:198.51.100.10',
    '-keyout', str(key), '-out', str(cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
upload = root / 'probe-upload.bin'
upload.write_bytes(os.urandom(128 * 1024))
seen = set()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        seen.add(self.client_address[0])
        size = 8 * 1024 * 1024 if self.path == '/long' else 1024 * 1024
        self.send_response(200)
        self.send_header('Content-Length', str(size))
        self.end_headers()
        try:
            for _ in range(size // 16384):
                self.wfile.write(b'x' * 16384)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass

    def do_POST(self):
        seen.add(self.client_address[0])
        remaining = int(self.headers.get('Content-Length', '0'))
        if remaining > 1024 * 1024:
            self.send_error(413)
            return
        while remaining:
            chunk = self.rfile.read(min(16384, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        self.send_response(200 if remaining == 0 else 400)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')

class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True

server = Server(('198.51.100.10', 18090), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
marker = 'kingline-bounded-load-probe'
rule = 'insert rule inet kingline_filter input ip saddr { ' + ', '.join(sorted(allowed_ips)) + ' } tcp dport 18090 accept comment "' + marker + '"\n'
subprocess.run(['nft', '-f', '-'], input=rule.encode(), check=True)
stop = threading.Event()
rss = []
report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'variant': label, 'source': 'Linux in Lithuania, not iOS or a Russian access network', 'profiles': []}

def transfer(port, kind):
    args = ['curl', '-sS', '--fail', '--max-time', '780' if soak else '50', '--cacert', str(cert),
            '--proxy', 'socks5h://127.0.0.1:' + str(port), '--output', '/dev/null',
            '--write-out', '%{http_code} %{size_download} %{time_total}']
    if kind == 'upload':
        args += ['--data-binary', '@' + str(upload)]
    else:
        args += ['--limit-rate', '12k' if soak and kind == 'long' else '256k']
    args += ['https://198.51.100.10:18090/' + ('long' if kind == 'long' else 'data')]
    result = subprocess.run(args, capture_output=True, text=True)
    return {'kind': kind, 'ok': result.returncode == 0 and result.stdout.startswith('200 '),
            'metrics': result.stdout.strip(), 'error_code': result.returncode}

try:
    client_binary = root / 'client-26.6.27/xray'
    runtime = ['-v', str(client_binary) + ':/tmp/client-xray:ro', '--entrypoint', '/tmp/client-xray'] if client_binary.exists() else ['--entrypoint', '/usr/local/bin/xray']
    report['client_core'] = '26.6.27' if client_binary.exists() else '26.7.28'
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', 'kingline-load-probe',
        '--network', 'host', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '-v', str(root / 'probe-client.json') + ':/tmp/probe.json:ro',
        *runtime, image, 'run', '-config', '/tmp/probe.json'],
        stdout=subprocess.DEVNULL, check=True)
    pid = int(subprocess.check_output(['docker', 'inspect', '-f', '{{.State.Pid}}', 'kingline-load-probe']))
    def memory():
        while not stop.wait(0.1):
            try:
                status = Path('/proc/' + str(pid) + '/status').read_text()
                rss.append(int(re.search(r'VmRSS:\s+(\d+)', status).group(1)))
            except (OSError, AttributeError):
                pass
    threading.Thread(target=memory, daemon=True).start()
    time.sleep(1)
    if soak:
        started = time.monotonic()
        last_burst = started
        report['bursts'] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(profile_names)) as pool:
            jobs = [pool.submit(transfer, port, 'long') for port in profile_ports]
            while not all(job.done() for job in jobs):
                concurrent.futures.wait(jobs, timeout=30)
                print('Long-lived tunnel test elapsed seconds:', int(time.monotonic() - started), flush=True)
                if time.monotonic() - last_burst >= 60 and not all(job.done() for job in jobs):
                    # Repeated short transfers model browsing while long transfers stay open.
                    with concurrent.futures.ThreadPoolExecutor(max_workers=36) as burst_pool:
                        short = [burst_pool.submit(transfer, port, kind)
                                 for port in profile_ports
                                 for kind in ['download'] * 8 + ['upload'] * 4]
                        checks = [job.result() for job in short]
                    row = {'elapsed_seconds': int(time.monotonic() - started),
                           'transfers': len(checks), 'passed': sum(c['ok'] for c in checks),
                           'linux_client_rss_mib': round((rss[-1] if rss else 0) / 1024, 1)}
                    report['bursts'].append(row)
                    print('Browsing burst:', json.dumps(row), flush=True)
                    last_burst = time.monotonic()
            for name, job in zip(profile_names, jobs):
                result = job.result()
                row = {'profile': name, 'parallel_transfers': 1, 'passed': int(result['ok']),
                       'long_transfer': result}
                report['profiles'].append(row)
                print(json.dumps(row), flush=True)
        report['duration_seconds'] = round(time.monotonic() - started, 1)
        report['linux_client_peak_rss_mib'] = round(max(rss or [0]) / 1024, 1)
    for index, name in ([] if soak else enumerate(profile_names)):
        port = 18081 + index
        before = len(rss)
        with concurrent.futures.ThreadPoolExecutor(max_workers=49) as pool:
            jobs = [pool.submit(transfer, port, 'long')]
            jobs += [pool.submit(transfer, port, 'download') for _ in range(32)]
            jobs += [pool.submit(transfer, port, 'upload') for _ in range(16)]
            results = [job.result() for job in jobs]
        row = {'profile': name, 'parallel_transfers': len(results),
               'passed': sum(r['ok'] for r in results), 'failed': [r for r in results if not r['ok']],
               'linux_client_peak_rss_mib': round(max(rss[before:] or [0]) / 1024, 1),
               'long_transfer': results[0]}
        report['profiles'].append(row)
        print(json.dumps(row), flush=True)
    report['observed_exit_ips'] = sorted(seen)
    assert seen <= allowed_ips
    (root / ('verification-load-' + label + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    assert all(row['passed'] == row['parallel_transfers'] for row in report['profiles'])
    assert all(row['passed'] == row['transfers'] for row in report.get('bursts', []))
finally:
    stop.set()
    subprocess.run(['docker', 'rm', '-f', 'kingline-load-probe'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rules = subprocess.check_output(['nft', '-a', 'list', 'chain', 'inet', 'kingline_filter', 'input']).decode()
    for line in rules.splitlines():
        if marker in line:
            handle = re.search(r'# handle (\d+)', line).group(1)
            subprocess.run(['nft', 'delete', 'rule', 'inet', 'kingline_filter', 'input', 'handle', handle], check=True)
    server.shutdown()
    server.server_close()
    for file in [cert, key, upload]:
        file.unlink(missing_ok=True)
