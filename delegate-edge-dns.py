#!/usr/bin/env python3
"""Run on the existing DNS credential host. Only adds the new edge delegation and glue."""
import datetime
import json
import os
from pathlib import Path
import shlex
import urllib.parse
import urllib.request

os.umask(0o077)
zone = '1e474adcf69dbdfe5d47b099715d11a5'
token = None
for line in Path('/root/.acme.sh/account.conf').read_text().splitlines():
    if line.startswith('SAVED_CF_Token='):
        token = shlex.split(line.split('=', 1)[1])[0]
assert token
def api(path, method='GET', data=None):
    request = urllib.request.Request('https://api.cloudflare.com/client/v4/zones/' + zone + path,
        method=method, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not result['success']:
        raise RuntimeError('DNS provider rejected update')
    return result['result']
assert api('')['name'] == 'kingline.online'
backup = Path('/root/backups') / ('edge-dns-before-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
backup.write_text(json.dumps(api('/dns_records?per_page=500'), indent=2))
for index, address in enumerate(['198.51.100.10', '198.51.100.31', '198.51.100.32'], 1):
    name = 'ns' + str(index) + '.edge.kingline.online'
    existing = api('/dns_records?' + urllib.parse.urlencode({'name': name, 'type': 'A'}))
    if existing:
        assert len(existing) == 1 and existing[0]['content'] == address and not existing[0]['proxied']
    else:
        api('/dns_records', 'POST', {'type': 'A', 'name': name, 'content': address, 'ttl': 300, 'proxied': False})
    existing = api('/dns_records?' + urllib.parse.urlencode({'name': 'edge.kingline.online', 'type': 'NS'}))
    if not any(item['content'].rstrip('.') == name for item in existing):
        api('/dns_records', 'POST', {'type': 'NS', 'name': 'edge.kingline.online', 'content': name, 'ttl': 300})
    print('Delegation ready:', name, address)
print('Previous DNS zone state backed up:', backup)
