#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys

os.umask(0o077)
root = Path('/opt/remnawave' if sys.argv[1] == 'core' else '/opt/remnanode')
dropin = Path('/etc/systemd/system/docker.service.d')
dropin.mkdir(exist_ok=True)
(dropin / '20-kingline-firewall.conf').write_text('[Unit]\nRequires=kingline-firewall.service\nAfter=kingline-firewall.service\n')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
lock = {}
for name in ('docker-compose.yml', 'docker-compose.override.yml'):
    path = root / name
    if not path.exists():
        continue
    text = path.read_text()
    def resolve(match):
        image = match.group(2)
        details = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
        digest = details['RepoDigests'][0]
        lock[image] = {'digest': digest, 'imageId': details['Id']}
        return match.group(1) + digest
    text = re.sub(r'(^\s*image:\s*)([^\s]+)', resolve, text, flags=re.M)
    path.write_text(text)
(root / 'image-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=root, check=True)
subprocess.run(['docker', 'compose', 'up', '-d'], cwd=root, check=True)
for original, value in lock.items():
    print(original, '->', value['digest'])
