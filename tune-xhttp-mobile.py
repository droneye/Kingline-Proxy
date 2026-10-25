#!/usr/bin/env python3
"""Keep XHTTP/REALITY, reduce streaming upload buffers on memory-limited clients."""
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
from remna_api import RemnaAPI

os.umask(0o077)
root = Path('/opt/kingline-control')
state = json.loads((root / 'owner-profile.json').read_text())
api = RemnaAPI()
current = next(p for p in api.items('config-profiles', 'configProfiles') if p['uuid'] == state['profile_uuid'])
if not (root / 'profile-vision-rollback.json').exists():
    (root / 'profile-vision-rollback.json').write_text(json.dumps(current, indent=2))
original = json.loads((root / 'profile-before-vision.json').read_text())
config = copy.deepcopy(original['config'])
inbound = next(i for i in config['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
inbound['settings']['flow'] = ''
inbound['streamSettings']['xhttpSettings']['mode'] = 'packet-up'
inbound['streamSettings']['xhttpSettings']['scMaxBufferedPosts'] = 8
extra = {'scMaxEachPostBytes': 32768, 'scMinPostsIntervalMs': 10,
         'xmux': {'maxConnections': 1, 'hKeepAlivePeriod': 30}}
# One underlying connection and no forced request-count/age rollover also keep
# XHTTP upload/download session affinity when the automatic DNS answer changes.
candidate = root / 'candidate-xhttp-mobile.json'
candidate.write_text(json.dumps(config))
image = 'remnawave/node@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d'
test = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '-v', str(candidate) + ':/tmp/config.json:ro', '--entrypoint', '/usr/local/bin/xray',
    image, 'run', '-test', '-config', '/tmp/config.json'], capture_output=True)
assert test.returncode == 0, 'Candidate configuration failed validation'
api.call('config-profiles', 'PATCH', {'uuid': current['uuid'], 'name': 'Kingline XHTTP Mobile', 'config': config})
hosts = [h for h in api.items('hosts', 'hosts') if h['inbound']['configProfileUuid'] == current['uuid']]
assert len(hosts) == 3
for host in hosts:
    api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'xhttpExtraParams': extra, 'muxParams': None})
state.update({'transport': 'xhttp', 'mode': 'packet-up', 'flow': '', 'xhttp_extra': extra,
              'mobile_revision_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
(root / 'owner-profile.json').write_text(json.dumps(state, indent=2))
interrupted = root / 'verification-load-vision-soak.json'
if interrupted.exists():
    report = json.loads(interrupted.read_text())
    report['status'] = 'intentionally_stopped_for_requested_xhttp_test; not a transport failure'
    interrupted.write_text(json.dumps(report, indent=2))
print('Three subscription entries updated: XHTTP packet-up, 32 KiB upload posts, one reusable connection')
