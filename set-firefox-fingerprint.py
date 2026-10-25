#!/usr/bin/env python3
"""Run on core; update only client fingerprints, preserving the working server transport."""
import datetime
import json
import os
from pathlib import Path
from remna_api import RemnaAPI

os.umask(0o077)
root = Path('/opt/kingline-control')
state_file = root / 'owner-profile.json'
state = json.loads(state_file.read_text())
api = RemnaAPI()
hosts = [h for h in api.items('hosts', 'hosts')
         if h['inbound']['configProfileUuid'] == state['profile_uuid']]
assert len(hosts) == 3 and state['transport'] == 'tcp'
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
backup = root / ('before-firefox-' + stamp + '.json')
backup.write_text(json.dumps({'hosts': hosts, 'state': state}, indent=2) + '\n')
try:
    for host in hosts:
        api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'fingerprint': 'firefox'})
    updated = api.items('hosts', 'hosts')
    assert all(next(h for h in updated if h['uuid'] == original['uuid'])['fingerprint'] == 'firefox'
               for original in hosts)
    state['fingerprint'] = 'firefox'
    state['fingerprint_revision_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state['fingerprint_rollback_file'] = str(backup)
    state_file.write_text(json.dumps(state, indent=2) + '\n')
except Exception:
    for host in hosts:
        api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'fingerprint': host['fingerprint']})
    state_file.write_text(json.dumps(json.loads(backup.read_text())['state'], indent=2) + '\n')
    raise
print('Firefox fingerprint applied to all three subscription endpoints; no node restart required')
