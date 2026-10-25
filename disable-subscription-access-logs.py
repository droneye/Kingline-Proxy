#!/usr/bin/env python3
"""The upstream page logs subscription secrets to stdout; don't retain that stream."""
import json
import os
from pathlib import Path
import re
import subprocess

os.umask(0o077)
root = Path('/opt/remnawave')
path = root / 'docker-compose.override.yml'
text = path.read_text()
start = text.index('  remnawave-subscription-page:\n')
end = re.search(r'\n  [a-zA-Z]', text[start:]).start() + start
block = text[start:end]
new, count = re.subn(r'\n    logging:\n(?:      [^\n]*\n?)+',
                     '\n    logging:\n      driver: none\n      options: !reset {}\n', block)
assert count == 1, 'Unexpected Compose shape; not modifying'
backup = path.with_name('docker-compose.override.yml.before-log-privacy')
if not backup.exists():
    backup.write_text(text)
path.write_text(text[:start] + new.rstrip('\n') + text[end:])
try:
    c = json.loads(subprocess.check_output(['docker', 'compose', 'config', '--format', 'json'], cwd=root))
    logging = c['services']['remnawave-subscription-page']['logging']
    assert logging['driver'] == 'none' and not logging.get('options')
except Exception:
    path.write_text(text)
    raise
subprocess.run(['docker', 'compose', 'up', '-d', '--no-deps', 'remnawave-subscription-page'], cwd=root, check=True)
print('Subscription request URLs no longer retained in container logs; container health monitoring remains enabled')
