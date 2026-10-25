"""Run on core. Issue a report-only token into a root-readable file, never stdout."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets

from .store import Store

parser = argparse.ArgumentParser()
parser.add_argument('name')
parser.add_argument('--region', default='RU-home')
parser.add_argument('--affect-routing', action='store_true', help='Enable only after validating the real probe agent')
args = parser.parse_args()
if not re.fullmatch('[a-zA-Z0-9_-]{1,50}', args.name):
    parser.error('invalid probe name')
root = Path('/opt/kingline-manager/data')
store = Store(root)
token = secrets.token_urlsafe(48)
with store.db() as db:
    db.execute('INSERT INTO probes VALUES (?,?,?,?)',
               (args.name, hashlib.sha256(token.encode()).hexdigest(), args.region, args.affect_routing))
file = root / ('probe-' + args.name + '.token')
with file.open('x') as output:
    file.chmod(0o600)
    output.write(token)
print('Report-only token saved to ' + str(file))
