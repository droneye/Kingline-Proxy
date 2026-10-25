#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess

os.umask(0o077)
root = Path('/opt/remnanode')
root.mkdir(mode=0o700, exist_ok=True)
key = (root / '.node-secret').read_text().strip()
assert key and '\n' not in key
(root / '.env').write_text('NODE_PORT=2222\nSECRET_KEY=' + key + '\n')
compose = '''services:
  remnanode:
    image: remnawave/node:3.4.2
    container_name: remnanode
    hostname: remnanode
    restart: always
    network_mode: host
    env_file: .env
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    logging:
      driver: local
      options:
        max-size: "10m"
        max-file: "3"
'''
compose_path = root / 'docker-compose.yml'
if compose_path.exists():
    raise SystemExit('Node already has a compose file; inspect before reinstalling')
compose_path.write_text(compose)
subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=root, check=True)
subprocess.run(['docker', 'compose', 'up', '-d'], cwd=root, check=True)
print('NODE_INSTALLED')
