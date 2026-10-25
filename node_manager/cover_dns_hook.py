"""Certbot DNS-01 hook for the existing three authoritative DNS replicas."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ZONE = 'edge.kingline.online.'
DOMAIN = ZONE.rstrip('.')


def dns_api(replica, method='GET', body=None):
    req = urllib.request.Request('http://' + replica['management_ip'] +
        ':8081/api/v1/servers/localhost/zones/' + ZONE, method=method,
        headers={'X-API-Key': replica['api_key'], 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def challenge(action):
    assert action in ['auth', 'cleanup']
    assert os.environ['CERTBOT_DOMAIN'] == DOMAIN
    value = os.environ['CERTBOT_VALIDATION']
    assert re.fullmatch(r'[A-Za-z0-9_-]{20,200}', value)
    name = '_acme-challenge.' + ZONE
    replicas = json.loads(Path('/opt/kingline-control/dns-replicas.json').read_text())
    content = json.dumps(value)
    for replica in replicas:
        zone = dns_api(replica)
        old = next((r for r in zone['rrsets'] if r['name'] == name and r['type'] == 'TXT'), None)
        values = {r['content'] for r in old['records']} if old else set()
        values.add(content) if action == 'auth' else values.discard(content)
        record = {'name': name, 'type': 'TXT', 'ttl': 30,
                  'changetype': 'REPLACE' if values else 'DELETE',
                  'records': [{'content': v, 'disabled': False} for v in sorted(values)]}
        dns_api(replica, 'PATCH', {'rrsets': [record]})
    if action == 'auth':
        for _ in range(30):
            ready = True
            for ip in [r['public_ip'] for r in replicas] + ['1.1.1.1', '8.8.8.8']:
                result = subprocess.run(['dig', '@' + ip, name, 'TXT', '+short', '+time=2', '+tries=1'],
                                        capture_output=True, text=True, timeout=5)
                ready = ready and content in result.stdout.splitlines()
            if ready:
                return
            time.sleep(2)
        raise RuntimeError('dns_challenge_propagation_timeout')


if __name__ == '__main__':
    try:
        challenge(sys.argv[1])
    except Exception:
        print('DNS certificate validation hook failed', file=sys.stderr)
        raise SystemExit(1)
