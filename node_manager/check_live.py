"""Read-only production verification plus rejected requests that cannot create jobs."""
import base64
import datetime
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from remna_api import RemnaAPI

root = Path('/opt/kingline-manager/data')
control = Path('/opt/kingline-control')
token = (root / 'admin.token').read_text().strip()


def call(path, authorized=True, body=None):
    headers = {'Content-Type': 'application/json'}
    if authorized:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        headers['Idempotency-Key'] = 'rejected-smoke-test'
    request = urllib.request.Request('http://127.0.0.1:8090' + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


assert call('/healthz', False)[1]['worker_alive']
assert call('/v1/nodes', False)[0] == 401
assert call('/v1/openapi.json', False)[0] == 401
assert call('/v1/probes/targets', True)[0] == 401  # Admin token is not a report token.
before = call('/v1/jobs')[1]
body = {'ip': '198.51.100.10', 'name': 'Blocked-test', 'label': 'blocked', 'country': 'LT',
        'quota_tb': 1, 'speed_mbps': 1000}
assert call('/v1/nodes', body=body)[0] == 409
assert call('/v1/nodes', body={**body, 'ip': '127.0.0.1'})[0] == 422
assert call('/v1/jobs')[1] == before
standard = call('/v1/standard')[1]
assert standard['fingerprint'] == 'firefox' and standard['flow'] == 'xtls-rprx-vision'
inventory = call('/v1/nodes')[1]
assert len(inventory['nodes']) == 2 and all(n['provisioned'] for n in inventory['nodes'])
assert all(r['ok'] and r['dns_answer_ready'] for r in inventory['routing']['replicas'])
api = RemnaAPI()
users = api.call('users')
assert users['total'] == 1 and users['users'][0]['username'] == 'vladislav'
assert all(not p['name'].startswith('KL-test-') for p in api.items('config-profiles', 'configProfiles'))
assert all(not p['name'].startswith('KL-test-') for p in api.items('nodes', 'nodes'))
state = json.loads((control / 'owner-profile.json').read_text())
squads = api.items('internal-squads', 'internalSquads')
for name in ['Default-Squad', 'Kingline Personal']:
    squad = next(s for s in squads if s['name'] == name)
    assert state['inbound_uuid'] in [i['uuid'] for i in squad['inbounds']]
req = urllib.request.Request('https://connect.kingline.online/' + state['user']['shortUuid'],
                             headers={'User-Agent': 'v2raytun/2.4.7'})
with urllib.request.urlopen(req, timeout=20) as response:
    subscription = base64.b64decode(response.read()).decode()
links = [urllib.parse.urlsplit(line) for line in subscription.splitlines() if line.startswith('vless://')]
assert len(links) == 3
assert len({(p.username, p.port, p.query) for p in links}) == 1, 'Profiles do not share identical settings'
with urllib.request.urlopen('https://panel.kingline.online/', timeout=20) as response:
    assert response.status == 200
report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'private_api_auth': 'passed', 'worker_running': True, 'protected_ip_rejected': True,
          'nonpublic_ip_rejected': True, 'rejected_requests_created_no_jobs': True,
          'existing_profiles_identical_except_address_and_label': True, 'profile_count': len(links),
          'production_user_count': users['total'], 'temporary_panel_resources_removed': True,
          'default_squad_has_standard_inbound': True, 'dns_replicas_healthy': True,
          'panel_http_status': 200, 'fresh_ubuntu_end_to_end_test': 'pending additional clean server'}
(control / 'verification-manager-live.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
