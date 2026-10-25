#!/usr/bin/env python3
"""Create a restricted baseline archive with database and exact current configuration."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import sqlite3

os.umask(0o077)
role = sys.argv[1]
stage = sys.argv[2] if len(sys.argv) > 2 else 'baseline'
assert stage in ['baseline', 'stage2']
root = Path('/opt/remnawave' if role == 'core' else '/opt/remnanode')
backup = Path('/root/backups') / ('remnawave-' + stage + '-' + role + '-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700, parents=True)
paths = [root / name for name in ['docker-compose.yml', 'docker-compose.override.yml', '.env', 'image-lock.json']]
paths += [Path(p) for p in ['/etc/wireguard', '/etc/kingline-firewall.nft',
    '/usr/local/sbin/kingline-firewall', '/etc/systemd/system/kingline-firewall.service',
    '/etc/systemd/system/docker.service.d/20-kingline-firewall.conf']]
if role == 'core':
    paths += [root / p for p in ['caddy/Caddyfile', 'subscription/.env', 'subscription/token-info.json', 'admin-credentials.json']]
    dump = backup / 'database.dump'
    with dump.open('wb') as output:
        subprocess.run(['docker', 'exec', 'remnawave-db', 'sh', '-c',
            'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'], stdout=output, check=True)
    with dump.open('rb') as source:
        result = subprocess.run(['docker', 'exec', '-i', 'remnawave-db', 'pg_restore', '--list'],
                                stdin=source, stdout=subprocess.DEVNULL, check=True)
else:
    paths.append(Path('/etc/fail2ban'))
dns_snapshot = None
manager_snapshot = None
if stage == 'stage2':
    paths += [Path(p) for p in ['/opt/kingline-dns/docker-compose.yml', '/opt/kingline-dns/pdns.conf',
        '/opt/kingline-dns/api.key', '/etc/sysctl.d/90-kingline-vpn.conf', '/etc/modules-load.d/kingline-bbr.conf']]
    if role != 'core':
        paths += [Path('/etc/ssh/sshd_config'), Path('/etc/ssh/sshd_config.d'),
                  Path('/root/.ssh/authorized_keys'), Path('/opt/kingline-cover'), Path('/opt/kingline-node')]
    database = Path('/opt/kingline-dns/data/pdns.sqlite3')
    if database.exists():
        dns_snapshot = backup / 'pdns.sqlite3'
        with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as source:
            with sqlite3.connect(dns_snapshot) as destination:
                source.backup(destination)
                assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    if role == 'core':
        paths += [root / 'subscription-guard/nginx.conf', Path('/etc/systemd/system/kingline-smart-dns.service')]
        paths += [p for p in Path('/opt/kingline-control').iterdir()
                  if p.is_file() and not p.name.startswith(('probe-', 'candidate-'))]
        manager = Path('/opt/kingline-manager')
        if manager.exists():
            paths += [manager / 'app', Path('/etc/systemd/system/kingline-node-manager.service'),
                      manager / 'data/cover',
                      Path('/etc/systemd/system/kingline-cover-maintenance.service'),
                      Path('/etc/systemd/system/kingline-cover-maintenance.timer'),
                      Path('/etc/systemd/system/kingline-tunnel-health.service'),
                      Path('/etc/systemd/system/kingline-tunnel-health.timer'),
                      Path('/usr/local/bin/kingline-node')]
            paths += [p for p in (manager / 'data').iterdir()
                      if p.is_file() and not p.name.startswith('manager.sqlite3') and p.name not in ['worker.lock', 'network.lock']]
            database = manager / 'data/manager.sqlite3'
            if database.exists():
                manager_snapshot = backup / 'manager.sqlite3'
                with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as source:
                    with sqlite3.connect(manager_snapshot) as destination:
                        source.backup(destination)
                        assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
archive = backup / 'configuration.tar.gz'
with tarfile.open(archive, 'w:gz') as output:
    for path in paths:
        if path.exists():
            output.add(path, arcname=str(path).lstrip('/'))
    if role == 'core':
        output.add(dump, arcname='database.dump')
    if dns_snapshot:
        output.add(dns_snapshot, arcname='opt/kingline-dns/data/pdns.sqlite3')
    if manager_snapshot:
        output.add(manager_snapshot, arcname='opt/kingline-manager/data/manager.sqlite3')
with tarfile.open(archive, 'r:gz') as source:
    members = source.getnames()
    assert any(name.endswith('docker-compose.yml') for name in members)
sha = hashlib.sha256(archive.read_bytes()).hexdigest()
(backup / 'SHA256SUMS').write_text(sha + '  configuration.tar.gz\n')
report = {'role': role, 'archive': str(archive), 'bytes': archive.stat().st_size,
          'sha256': sha, 'database_dump_verified': role == 'core', 'dns_database_verified': dns_snapshot is not None,
          'manager_database_verified': manager_snapshot is not None}
(root / (stage + '-backup.json')).write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
