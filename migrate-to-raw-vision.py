#!/usr/bin/env python3
"""Run on core: migrate the existing three endpoints to TCP/RAW + REALITY + Vision."""
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
from remna_api import RemnaAPI

os.umask(0o077)
root = Path('/opt/kingline-control')
state_file = root / 'owner-profile.json'
state = json.loads(state_file.read_text())
api = RemnaAPI()
profile = next(p for p in api.items('config-profiles', 'configProfiles')
               if p['uuid'] == state['profile_uuid'])
hosts = [h for h in api.items('hosts', 'hosts')
         if h['inbound']['configProfileUuid'] == profile['uuid']]
assert len(hosts) == 3, 'Unexpected endpoint count; no changes made'
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
backup = root / ('before-raw-vision-' + stamp)
backup.mkdir(mode=0o700)
for name, value in [('profile', profile), ('hosts', hosts), ('owner-profile', state)]:
    (backup / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')

config = copy.deepcopy(profile['config'])
inbound = next(i for i in config['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
# Retain the historical tag and UUID because hosts and squads reference them.
inbound['settings']['flow'] = 'xtls-rprx-vision'
for client in inbound['settings'].get('clients', []):
    client['flow'] = 'xtls-rprx-vision'
stream = inbound['streamSettings']
stream['network'] = 'tcp'  # Compatible TCP alias of RAW on the deployed cores and clients.
stream.pop('method', None)
stream.pop('xhttpSettings', None)
stream.pop('tcpSettings', None)
stream.pop('rawSettings', None)
assert stream['security'] == 'reality' and inbound['port'] == 443
candidate = root / 'candidate-raw-vision.json'
candidate.write_text(json.dumps(config))
image = 'remnawave/node@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d'
for client in [False, True]:
    runtime = ['--entrypoint', '/usr/local/bin/xray']
    if client:
        runtime = ['-v', str(root / 'client-26.6.27/xray') + ':/tmp/client-xray:ro',
                   '--entrypoint', '/tmp/client-xray']
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '-v', str(candidate) + ':/tmp/config.json:ro', *runtime,
        image, 'run', '-test', '-config', '/tmp/config.json'], capture_output=True)
    assert result.returncode == 0, 'Candidate rejected; live settings unchanged'

try:
    api.call('config-profiles', 'PATCH', {
        'uuid': profile['uuid'], 'name': 'Kingline RAW REALITY Vision', 'config': config})
    for host in hosts:
        api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'xhttpExtraParams': None,
                                   'muxParams': None})
    current = api.call('config-profiles/' + profile['uuid'])
    live = next(i for i in current['inbounds'] if i['tag'] == inbound['tag'])
    assert live['uuid'] == state['inbound_uuid'], 'Inbound identity changed'
    state['transport'] = 'tcp'
    state['flow'] = 'xtls-rprx-vision'
    state['mobile_revision_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state['transport_rollback_directory'] = str(backup)
    state.pop('mode', None)
    state.pop('xhttp_extra', None)
    state_file.write_text(json.dumps(state, indent=2) + '\n')
except Exception:
    api.call('config-profiles', 'PATCH', {
        'uuid': profile['uuid'], 'name': profile['name'], 'config': profile['config']})
    for host in hosts:
        api.call('hosts', 'PATCH', {k: host.get(k) for k in ['uuid', 'xhttpExtraParams', 'muxParams']})
    state_file.write_text((backup / 'owner-profile.json').read_text())
    raise
print('Three endpoints migrated to TCP/RAW + REALITY + Vision; identity and subscription URL retained')
print('Rollback directory:', backup)
