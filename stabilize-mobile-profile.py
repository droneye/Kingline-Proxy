#!/usr/bin/env python3
"""One-time, reversible XHTTP -> TCP/Vision migration on the control server."""
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
profile = next(p for p in api.items('config-profiles', 'configProfiles') if p['uuid'] == state['profile_uuid'])
backup = root / 'profile-before-vision.json'
if not backup.exists():
    backup.write_text(json.dumps(profile, indent=2))
config = copy.deepcopy(profile['config'])
inbound = next(i for i in config['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
# Keep the tag/UUID stable: hosts, squads and user authorization already reference it.
inbound['settings']['flow'] = 'xtls-rprx-vision'
inbound['streamSettings']['network'] = 'tcp'
inbound['streamSettings'].pop('xhttpSettings', None)
candidate = root / 'candidate-vision.json'
candidate.write_text(json.dumps(config))
image = 'remnawave/node@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d'
test = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '-v', str(candidate) + ':/tmp/config.json:ro', '--entrypoint', '/usr/local/bin/xray',
    image, 'run', '-test', '-config', '/tmp/config.json'], capture_output=True)
assert test.returncode == 0, 'Candidate configuration failed validation; live configuration unchanged'
api.call('config-profiles', 'PATCH', {'uuid': profile['uuid'], 'name': 'Kingline Mobile REALITY', 'config': config})
state['transport'] = 'tcp'
state['flow'] = 'xtls-rprx-vision'
state['mobile_revision_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
(root / 'owner-profile.json').write_text(json.dumps(state, indent=2))
print('TCP + REALITY + Vision applied; subscription URL and three endpoint names preserved; rollback saved')
