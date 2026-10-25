#!/usr/bin/env python3
"""On core: subscription compatibility and per-IP throttling, without printing credentials."""
import base64
import collections
import concurrent.futures
import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.request
import urllib.parse

root = Path('/opt/kingline-control')
state = json.loads((root / 'owner-profile.json').read_text())
base = 'https://connect.kingline.online/'
def get(path, browser=False, spoof=None):
    headers = {'User-Agent': 'Mozilla/5.0' if browser else 'v2raytun/2.4.7',
               'Accept': 'text/html' if browser else '*/*'}
    if spoof:
        headers['X-Real-IP'] = spoof
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=12) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as error:
        return error.code, b'', {}

status, data, headers = get(state['user']['shortUuid'])
links = base64.b64decode(data).decode().splitlines()
assert status == 200 and len(links) == 3
for link in links:
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert params['type'][0] == state['transport']
    if state.get('xhttp_extra'):
        assert params['mode'][0] == state['mode']
        assert json.loads(params['extra'][0]) == state['xhttp_extra']
html_status, html, _ = get(state['user']['shortUuid'], browser=True)
assert html_status == 200 and b'<html' in html.lower()
random_path = secrets.token_urlsafe(16)
with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
    codes = list(pool.map(lambda i: get(random_path, spoof='203.0.113.' + str(1 + i % 100))[0], range(100)))
counts = dict(collections.Counter(codes))
assert 429 in counts, 'No rate limiting observed'
time.sleep(5)
assert get(state['user']['shortUuid'])[0] == 200
report = {'valid_subscription': 200, 'profile_count': len(links), 'transport': state['transport'],
          'mobile_extra_present': bool(state.get('xhttp_extra')),
          'browser_page': html_status, 'random_code_bits': 128,
          'code_characters': len(state['user']['shortUuid']),
          'burst_status_codes_with_spoofed_x_real_ip': counts, 'recovery_after_burst': 200,
          'update_interval_hours': next((v for k, v in headers.items() if k.lower() == 'profile-update-interval'), None)}
(root / 'verification-subscription-stage2.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report))
